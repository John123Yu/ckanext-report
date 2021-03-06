import datetime
from ckan.lib.helpers import json as j1
import ckan.plugins.toolkit as t
import ckanext.report.helpers as dguhelpers
from ckanext.report.report_registry import ReportRegistry
from ckan.lib.render import TemplateNotFound
from ckanext.report.json import DateTimeJsonEncoder
#from ckan.common import OrderedDict
from ckan.lib.helpers import OrderedDict

c = t.c


class ReportController(t.BaseController):

    def index(self):
        registry = ReportRegistry.instance()
        c.reports = registry.get_reports()
        return t.render('report/index.html')

    def view(self, report_name, organization=None, refresh=False):
        c.report_name = report_name
        try:
            report = ReportRegistry.instance().get_report(report_name)
        except KeyError:
            t.abort(404, 'Report not found')

        # ensure correct url is being used
        if 'organization' in t.request.environ['pylons.routes_dict'] and \
            'organization' not in report.option_defaults:
                t.redirect_to(dguhelpers.dgu_relative_url_for(organization=None))
        elif 'organization' not in t.request.environ['pylons.routes_dict'] and\
            'organization' in report.option_defaults and \
            report.option_defaults['organization']:
                org = report.option_defaults['organization']
                t.redirect_to(dguhelpers.dgu_relative_url_for(organization=org))
        if 'organization' in t.request.params:
            # organization should only be in the url - let the param overwrite
            # the url.
            t.redirect_to(dguhelpers.dgu_relative_url_for())

        # options
        c.options = report.add_defaults_to_options(t.request.params)
        if 'format' in c.options:
            format = c.options.pop('format')
        else:
            format = None
        if 'organization' in report.option_defaults:
            c.options['organization'] = organization
            c.offer_organization_index = \
                report.option_defaults['organization'] is None

        c.report_title = report.title
        c.report_description = report.description

        # Refresh the cache if requested
        if t.request.method == 'POST' and not format:
            if not (c.userobj and c.userobj.sysadmin):
                t.abort(401)
            report.refresh_cache(c.options)

        # Alternative way to refresh the cache - not in the UI, but is
        # handy for testing
        try:
            refresh = t.asbool(t.request.params.get('refresh'))
        except ValueError:
            refresh = False
        if refresh:
            if not (c.userobj and c.userobj.sysadmin):
                t.abort(401)
            c.options.pop('refresh')
            report.refresh_cache(c.options)
            # Don't want the refresh=1 in the url once it is done
            t.redirect_to(dguhelpers.dgu_relative_url_for(refresh=None))

        # Check for any options not allowed by the report
        for key in c.options:
            if key not in report.option_defaults:
                t.abort(400, 'Option not allowed by report: %s' % key)

        try:
            c.data, c.report_date = report.get_fresh_report(**c.options)
        except t.ObjectNotFound:
            t.abort(404)

        if format and format != 'html':
            
            ensure_data_is_dicts(c.data)
            anonymise_user_names(c.data, organization=c.options.get('organization'))
            
            sorted_list = []
            for data in c.data:
              for row in data['table']: 
                 temp = OrderedDict()              
                 for k, v in sorted(row.items(), key=lambda x: data['order'].get(x[0])):
                   temp.update({k:v})
                 sorted_list.append(temp)  
            
              data['table'] = sorted_list
              
            if format == 'csv':
                filename = 'report_%s.csv' % report.generate_key(c.options).replace('?', '_')
                t.response.headers['Content-Type'] = 'application/csv'
                t.response.headers['Content-Disposition'] = str('attachment; filename=%s' % (filename))        
                for data in c.data:     
                  return make_csv_from_dicts(data['table'])
            elif format == 'json':
                filename = 'report_%s.json' % report.generate_key(c.options).replace('?', '_')
                t.response.headers['Content-Type'] = 'application/json'
                t.response.headers['Content-Disposition'] = str('attachment; filename=%s' % (filename))
                result = OrderedDict()
                result['generated_at'] = c.report_date
                for data in c.data:
                  result['table'] = data['table']
                return j1.dumps(result, cls=DateTimeJsonEncoder)
            else:
                t.abort(400, 'Format not known - try html, json or csv')

        c.are_some_results = bool(c.data['table'] if 'table' in c.data
                                  else c.data)
        if c.are_some_results:
            # you can't pass args into genshi template, so it will just look
            # for c.data
            c.report_snippet = t.render_snippet(report.get_template())
        return t.render('report/view.html')


def make_csv_from_dicts(rows):
    import csv
    import cStringIO as StringIO

    csvout = StringIO.StringIO()
    csvwriter = csv.writer(
        csvout,
        dialect='excel',
        quoting=csv.QUOTE_NONNUMERIC
    )
    # extract the headers by looking at all the rows and
    # get a full list of the keys, retaining their ordering
    headers_ordered = []
    headers_set = set()
    for row in rows:
        new_headers = set(row.keys()) - headers_set
        headers_set |= new_headers
        for header in row.keys():
            if header in new_headers:
                headers_ordered.append(header)
    csvwriter.writerow(headers_ordered)
    for row in rows:
        items = []
        for header in headers_ordered:
            item = row.get(header, 'no record')
            if isinstance(item, datetime.datetime):
                item = item.strftime('%Y-%m-%d %H:%M')
            elif isinstance(item, (int, long, float, list, tuple)):
                item = unicode(item)
            elif item is None:
                item = ''
            else:
                item = item.encode('utf8')
            items.append(item)
        try:
            csvwriter.writerow(items)
        except Exception, e:
            raise Exception("%s: %s, %s" % (e, row, items))
    csvout.seek(0)
    return csvout.read()


def ensure_data_is_dicts(dataDict):
    '''Ensure that the data is a list of dicts, rather than a list of tuples
    with column names, as sometimes is the case. Changes it in place'''
    for data in dataDict:       
      if data['table'] and isinstance(data['table'][0], (list, tuple)):
        new_data = []
        columns = data['columns']
        for row in data['table']:
            new_data.append(OrderedDict(zip(columns, row)))
        data['table'] = new_data
        del data['columns']


def anonymise_user_names(dataDict, organization=None):
    '''Ensure any columns with names in are anonymised, unless the current user
    has privileges.'''
    for data in dataDict:
      column_names = data['table'][0].keys() if data['table'] else []
      for col in column_names:
        if col.lower() in ('user', 'username', 'user name', 'author'):
            for row in data['table']:
                row[col] = dguhelpers.user_link_info(row[col],
                              organisation=organization)[0]
