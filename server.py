# -*- coding: utf-8 -*-
import os
import io
import csv
from datetime import datetime, timedelta

from clastic import Application, render_basic, Middleware
from clastic.meta import MetaApplication
from clastic.render import AshesRenderFactory
from clastic.static import StaticApplication

from ashes import escape_html

from log import tlog

from boltons.strutils import find_hashtags
from boltons.tbutils import ExceptionInfo

from dal import HashtagDatabaseConnection 
from common import PAGINATION, MAX_DB_ROW
from utils import encode_vals, to_unicode


TEMPLATES_PATH = 'templates'
STATIC_PATH = 'static'
_CUR_PATH = os.path.dirname(__file__)


Database = HashtagDatabaseConnection()


def format_timestamp(timestamp, inc_time=True):
    _timestamp_pattern = '%Y%m%d%H%M%S'
    timestamp = datetime.strptime(timestamp, _timestamp_pattern)
    if inc_time:
        ret = timestamp.strftime('%e %b %Y %H:%M:%S')
    else:
        ret = timestamp.strftime('%e %b %Y')
    return ret


def format_revs(rev):
    url_str = 'https://{lang}.wikipedia.org/wiki/?diff={this}&oldid={last}'
    user_url = 'https://{lang}.wikipedia.org/wiki/User:{user}'
    if rev['htrc_lang'] == 'wikidata':
        url_str = 'https://www.wikidata.org/wiki/?diff={this}&oldid={last}'
        user_url = 'https://www.wikidata.org/wiki/User:{user}'
    rev['rc_user_url'] = user_url.format(lang=rev['htrc_lang'],
                                         user=rev['rc_user_text'])
    rev['spaced_title'] = rev.get('rc_title', '').replace('_', ' ')
    rev['diff_size'] = rev['rc_new_len'] - rev['rc_old_len']
    rev['date'] = format_timestamp(rev['rc_timestamp'])
    rev['diff_url'] = url_str.format(lang=rev['htrc_lang'],
                                     this=rev['rc_this_oldid'],
                                     last=rev['rc_last_oldid'])
    try:
        rev['rc_comment'] = escape_html(rev['rc_comment'])
    except Exception as e:
        pass
    rev['rc_comment_plain'] = rev['rc_comment']
    rev['rc_comment'] = to_unicode(rev['rc_comment'])
    rev['tags'] = find_hashtags(rev['rc_comment'])
    for tag in rev['tags']:
        # TODO: Turn @mentions into links
        link = '<a href="/hashtags/search/%s">#%s</a>' % (tag, tag)
        new_comment = rev['rc_comment'].replace('#%s' % tag, link)
        rev['rc_comment'] = new_comment
    return rev


def calculate_pages(offset, total, pagination):
    # Check if there is a previous page
    if offset == 0:
        prev = -1
    elif (offset - pagination) < 0:
        prev = 0
    else:
        prev = offset - pagination
    # Check if there is a next page
    if (offset + pagination) >= total:
        next = -1
    else:
        next = offset + pagination
    return prev, next


def format_stats(stats):
    stats['bytes'] = '{:,}'.format(int(stats['bytes']))
    stats['revisions'] = '{:,}'.format(stats['revisions'])
    stats['pages'] = '{:,}'.format(stats['pages'])
    stats['users'] = '{:,}'.format(stats['users'])
    stats['newest'] = format_timestamp(stats['newest'], inc_time=False)
    stats['oldest'] = format_timestamp(stats['oldest'], inc_time=False)
    return stats


def home():
    with tlog.critical('home') as rec:
        top_tags = Database.get_top_hashtags()
        for tag in top_tags:
            # TODO: cleaner data input
            tag['ht_text'] = tag['ht_text'].decode('utf8', errors='replace')

        langs = Database.get_langs()
        rec.success('Homepage ready')

    return {'top_tags': top_tags,
            'langs': [l['htrc_lang'] for l in langs]}


def generate_csv(request, tag):
    lang = request.values.get('lang')
    limit = request.values.get('limit', 20000)
    tag = tag.lower()
    tag = tag.encode('utf8')
    revs = Database.get_hashtags(tag, lang=lang, end=limit)
    output = io.BytesIO()
    fieldnames = ['htrc_lang', 'date', 'diff_url', 'rc_user_text',
                  'spaced_title', 'tags', 'rc_comment_plain', 'diff_size',
                  'rc_cur_id', 'rc_last_oldid', 'rc_old_len',
                  'rc_this_oldid', 'rc_new_len', 'rc_id',
                  'rc_namespace', 'rc_source', 'rc_type', 'rc_logid',
                  'rc_log_action', 'rc_log_type', 'rc_minor',
                  'rc_bot', 'rc_patrolled', 'rc_params', 'rc_new',
                  'rc_deleted', 'rc_user', 'rc_timestamp', 'ht_text',
                  'ht_id']
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    output.write('\xEF\xBB\xBF')
    writer.writeheader()
    for rev in revs:
        # TODO: better organization
        formatted_rev = format_revs(rev)
        writer.writerow(encode_vals(formatted_rev))
    ret = output.getvalue()
    return ret


def generate_report(request, tag=None, offset=0):
    lang = request.values.get('lang')
    offset = int(offset)
    if tag:
        tag = tag.encode('utf8')
        tag = tag.lower()
    revs = Database.get_hashtags(tag, lang=lang, start=offset)
    langs = Database.get_langs()
    # TODO: Get RevScore per rev
    # https://meta.wikimedia.org/wiki/Objective_Revision_Evaluation_Service
    if not revs:
        return {'revisions': [],
                'tag': tag,
                'stats': {},
                'page': {},
                'lang': lang}
    stats = Database.get_hashtag_stats(tag, lang=lang)
    stats = format_stats(stats[0])
    ret = [format_revs(rev) for rev in revs]
    prev, next = calculate_pages(offset, 
                                 int(stats['revisions'].replace(',', '')),
                                 PAGINATION)
    page = {'start': offset + 1, 
            'end': offset + len(revs),
            'prev': prev,
            'next': next}
    return {'revisions': ret, 
            'tag': tag, 
            'stats': stats,
            'page': page,
            'lang': lang,
            'langs': [l['htrc_lang'] for l in langs]}


def create_app():
    _template_dir = os.path.join(_CUR_PATH, TEMPLATES_PATH)
    _static_dir = os.path.join(_CUR_PATH, STATIC_PATH)
    templater = AshesRenderFactory(_template_dir)
    # TODO: Add support for @mentions
    routes = [('/', home, 'index.html'),
              ('/docs', home, 'docs.html'),
              ('/search/', generate_report, 'report.html'),
              ('/search/all', generate_report, 'report.html'),
              ('/search/all/<offset>', generate_report, 'report.html'),
              ('/search/<tag>', generate_report, 'report.html'),
              ('/csv/<tag>', generate_csv, render_basic),
              ('/search/<tag>/<offset>', generate_report, 'report.html'),
              ('/static', StaticApplication(_static_dir)),
              ('/meta/', MetaApplication())]
    return Application(routes, 
                       middlewares=[],
                       render_factory=templater)

class FakeReq(object):
    def __init__(self):
        self.values = {'lang': 'en'}

Req = FakeReq()

if __name__ == '__main__':
    app = create_app()
    app.serve()
