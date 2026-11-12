# coding: utf-8
from unittest import TestCase

from lxml import etree

from sickle.models import Record
from sickle.response import XMLParser
from sickle.utils import MetadataField, MetadataValue, project_metadata


OAI = 'http://www.openarchives.org/OAI/2.0/'
ALPHA = 'urn:alpha'
BETA = 'urn:beta'


def _clark(namespace):
    return '{%s}' % namespace


METADATA_PATH = '/{oai}OAI-PMH/{oai}GetRecord/{oai}record/{oai}metadata'.format(
    oai=_clark(OAI))


def oai_record(payload, prefix):
    return '''<OAI-PMH xmlns="{oai}" xmlns:{prefix}="{payload_ns}">
  <GetRecord>
    <record>
      <header>
        <identifier>oai:example.org:1</identifier>
        <datestamp>2020-01-01</datestamp>
      </header>
      <metadata>
        {payload}
      </metadata>
    </record>
  </GetRecord>
</OAI-PMH>'''.format(
        oai=OAI,
        prefix=prefix,
        payload_ns=payload.get('namespace'),
        payload=payload['xml'].format(prefix=prefix))


PREFIX_ALPHA_RECORD = oai_record({
    'namespace': ALPHA,
    'xml': '''<{prefix}:root xmlns:{prefix}="urn:alpha" xmlns:other="urn:beta">
          <{prefix}:title>Alpha title</{prefix}:title>
          <other:title>Beta title</other:title>
          <{prefix}:title type="primary">Second alpha title</{prefix}:title>
          <{prefix}:item id="one">
            <{prefix}:title>Nested title</{prefix}:title>
          </{prefix}:item>
          <{prefix}:empty></{prefix}:empty>
          <{prefix}:children><{prefix}:title>Child title</{prefix}:title></{prefix}:children>
          <{prefix}:mixed>Direct <{prefix}:span>descendant</{prefix}:span> text</{prefix}:mixed>
        </{prefix}:root>''',
}, 'alpha')


PREFIX_BETA_RECORD = oai_record({
    'namespace': ALPHA,
    'xml': '''<{prefix}:root xmlns:{prefix}="urn:alpha" xmlns:other="urn:beta">
          <{prefix}:title>Alpha title</{prefix}:title>
          <other:title>Beta title</other:title>
          <{prefix}:title type="primary">Second alpha title</{prefix}:title>
          <{prefix}:item id="one">
            <{prefix}:title>Nested title</{prefix}:title>
          </{prefix}:item>
          <{prefix}:empty></{prefix}:empty>
          <{prefix}:children><{prefix}:title>Child title</{prefix}:title></{prefix}:children>
          <{prefix}:mixed>Direct <{prefix}:span>descendant</{prefix}:span> text</{prefix}:mixed>
        </{prefix}:root>''',
}, 'z')


NESTED_RECORD = '''<OAI-PMH xmlns="{oai}">
  <GetRecord>
    <record>
      <header>
        <identifier>oai:example.org:2</identifier>
        <datestamp>2020-01-02</datestamp>
      </header>
      <metadata>
        <root xmlns="{alpha}">
          <name>Top name one</name>
          <group>
            <name>Nested name one</name>
            <name>Nested name two</name>
          </group>
          <name>Top name two</name>
        </root>
      </metadata>
    </record>
  </GetRecord>
</OAI-PMH>'''.format(oai=OAI, alpha=ALPHA)


FIELDS = [
    MetadataField('alpha_titles', '/%sroot/%stitle' % (_clark(ALPHA), _clark(ALPHA))),
    MetadataField('beta_titles', '/%sroot/%stitle' % (_clark(ALPHA), _clark(BETA))),
    MetadataField('item_ids', '/%sroot/%sitem' % (_clark(ALPHA), _clark(ALPHA)), attribute='id'),
    MetadataField('nested_titles',
                   '/%sroot/%sitem/%stitle' % (_clark(ALPHA), _clark(ALPHA), _clark(ALPHA))),
    MetadataField('missing', '/%sroot/%sabsent' % (_clark(ALPHA), _clark(ALPHA))),
    MetadataField('empty', '/%sroot/%sempty' % (_clark(ALPHA), _clark(ALPHA))),
    MetadataField('children_only', '/%sroot/%schildren' % (_clark(ALPHA), _clark(ALPHA))),
    MetadataField('mixed', '/%sroot/%smixed' % (_clark(ALPHA), _clark(ALPHA))),
]


def record_from_xml(xml):
    return Record(etree.fromstring(xml.encode('utf-8'), parser=XMLParser))


def as_serializable(projection):
    return dict(
        (field, [value.as_dict() for value in values])
        for field, values in projection.items())


class TestMetadataProjection(TestCase):

    def test_prefix_change_does_not_change_projection(self):
        first = record_from_xml(PREFIX_ALPHA_RECORD)
        second = record_from_xml(PREFIX_BETA_RECORD)

        self.assertEqual(as_serializable(first.project_metadata(FIELDS)),
                         as_serializable(second.project_metadata(FIELDS)))

        projection = first.project_metadata(FIELDS)
        self.assertEqual([value.value for value in projection['alpha_titles']],
                         ['Alpha title', 'Second alpha title'])
        self.assertEqual([value.value for value in projection['beta_titles']],
                         ['Beta title'])
        self.assertEqual([value.source_path for value in projection['alpha_titles']], [
            METADATA_PATH + '/%sroot/%stitle' % (_clark(ALPHA), _clark(ALPHA)),
            METADATA_PATH + '/%sroot/%stitle' % (_clark(ALPHA), _clark(ALPHA)),
        ])
        self.assertEqual([value.source_line for value in projection['alpha_titles']],
                         [10, 12])
        self.assertEqual(projection['item_ids'][0].value, 'one')
        self.assertEqual(projection['item_ids'][0].source_path,
                         METADATA_PATH + '/%sroot/%sitem/@id' % (_clark(ALPHA), _clark(ALPHA)))
        self.assertEqual(projection['nested_titles'][0].source_line, 14)
        self.assertEqual(projection['missing'], [])
        self.assertEqual(projection['empty'][0].value, '')
        self.assertIsNone(projection['children_only'][0].value)
        self.assertEqual(projection['mixed'][0].value, 'Direct ')

    def test_does_not_merge_same_named_elements_from_different_paths(self):
        record = record_from_xml(NESTED_RECORD)
        projection = record.project_metadata([
            ('top_names', '/%sroot/%sname' % (_clark(ALPHA), _clark(ALPHA))),
            ('nested_names',
             '/%sroot/%sgroup/%sname' % (_clark(ALPHA), _clark(ALPHA), _clark(ALPHA))),
        ])

        self.assertEqual([value.value for value in projection['top_names']],
                         ['Top name one', 'Top name two'])
        self.assertEqual([value.value for value in projection['nested_names']],
                         ['Nested name one', 'Nested name two'])
        self.assertEqual(
            [value.source_path for value in projection['top_names']],
            [METADATA_PATH + '/%sroot/%sname' % (_clark(ALPHA), _clark(ALPHA)),
             METADATA_PATH + '/%sroot/%sname' % (_clark(ALPHA), _clark(ALPHA))])
        nested_name_path = METADATA_PATH + '/%sroot/%sgroup/%sname' % (
            _clark(ALPHA), _clark(ALPHA), _clark(ALPHA))
        self.assertEqual(
            [value.source_path for value in projection['nested_names']],
            [nested_name_path, nested_name_path])

    def test_projection_leaves_default_metadata_shape_unchanged(self):
        record = record_from_xml(PREFIX_ALPHA_RECORD)

        self.assertEqual(record.metadata, {
            'title': [
                'Alpha title',
                'Beta title',
                'Second alpha title',
                'Nested title',
                'Child title',
            ],
            'item': [None],
            'empty': [None],
            'children': [None],
            'mixed': ['Direct '],
            'span': ['descendant'],
        })
        self.assertIsInstance(record.project_metadata(FIELDS)['alpha_titles'][0],
                              MetadataValue)

    def test_dict_spec_and_missing_attribute(self):
        record = record_from_xml(PREFIX_ALPHA_RECORD)
        projection = record.project_metadata([
            ('title_types', {
                'path': '/%sroot/%stitle' % (_clark(ALPHA), _clark(ALPHA)),
                'attribute': 'type'}),
        ])

        self.assertEqual([value.value for value in projection['title_types']],
                         [None, 'primary'])
        self.assertEqual(
            projection['title_types'][0].source_path,
            METADATA_PATH + '/%sroot/%stitle/@type' % (_clark(ALPHA), _clark(ALPHA)))

        mapping_projection = record.project_metadata({
            'alpha_titles': '/%sroot/%stitle' % (_clark(ALPHA), _clark(ALPHA)),
        })
        self.assertEqual([value.value for value in mapping_projection['alpha_titles']],
                         ['Alpha title', 'Second alpha title'])

    def test_invalid_path_is_rejected(self):
        tree = etree.fromstring(b'<root xmlns="urn:alpha"><title/></root>')
        with self.assertRaises(ValueError):
            project_metadata(tree, [('title', '/root/title')])

    def test_empty_namespace_selects_unqualified_elements(self):
        tree = etree.fromstring(b'<root><title>Local title</title></root>')
        projection = project_metadata(
            tree, [('title', '/{}root/{}title')])
        self.assertEqual(projection['title'][0].value, 'Local title')
        self.assertEqual(projection['title'][0].source_path,
                         '/{}root/{}title')
