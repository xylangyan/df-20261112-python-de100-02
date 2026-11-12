.. _customizing:

=============================================
Harvesting other Metadata Formats than OAI-DC
=============================================

By default, Sickle's mapping of the record XML into Python
dictionaries is tailored to work only with Dublin-Core-encoded metadata
payloads. Other formats most probably won't be mapped correctly,
especially if they are more hierarchically structured than Dublin
Core.

In case your want to harvest these more complex formats, you have to
write your own record model class by subclassing the default
implementation that unpacks the metadata XML::

    from sickle.models import Record

    class MyRecord(Record):
        # Your XML unpacking implementation goes here.
        pass

.. note::

    Take a look at the implementation of :class:`sickle.models.Record`
    to get an idea of how to do this.

Next, associate your implementation with OAI verbs in the
:class:`~sickle.app.Sickle` object. In this case, we want the
:class:`~sickle.app.Sickle` object to use our implementation to represent
items returned by ListRecords and GetRecord responses::

    sickle = Sickle('http://...')
    sickle.class_mapping['ListRecords'] = MyRecord
    sickle.class_mapping['GetRecord'] = MyRecord

If you need to rewrite *all* item implementations, you can also provide a
complete mapping to the :class:`~sickle.app.Sickle` object at instantiation::

    my_mapping = {
        'ListRecords': MyRecord,
        'GetRecord': MyRecord,
        # ...
    }

    sickle = Sickle('http://...', class_mapping=my_mapping)


Explicit Metadata Projection
============================

The default :attr:`sickle.models.Record.metadata` representation keeps its
historical, prefix-stripped dictionary shape. It is therefore possible for
elements with the same local name, but different namespaces or paths, to end
up under the same dictionary key.

When the harvesting code needs a stable field set, use
:meth:`sickle.models.Record.project_metadata` instead. Fields are selected with
namespace URIs and absolute element paths in Clark notation. Paths start at
the metadata container element (``oai_dc:dc``, ``mods:mods``, and so on)::

    from sickle.utils import MetadataField

    fields = [
        MetadataField(
            'dc_title',
            '/{http://www.openarchives.org/OAI/2.0/oai_dc/}dc/'
            '{http://purl.org/dc/elements/1.1/}title'),
        MetadataField(
            'mods_title',
            '/{http://www.loc.gov/mods/v3}mods/'
            '{http://www.loc.gov/mods/v3}titleInfo/'
            '{http://www.loc.gov/mods/v3}title'),
        MetadataField(
            'identifier_type',
            '/{http://www.openarchives.org/OAI/2.0/oai_dc/}dc/'
            '{http://purl.org/dc/elements/1.1/}identifier',
            attribute='type'),
    ]

    projection = record.project_metadata(fields)

Each key in ``projection`` contains a list of
:class:`sickle.utils.MetadataValue` objects in XML document order. A value
exposes ``.value``, ``.source_path``, and ``.source_line`` (or ``.as_dict()``).
Since paths are matched against expanded namespace URIs and each path segment
must match exactly, changing prefixes does not change the projection and
same-named elements on different paths are not merged. Attribute specifications
accept Clark notation as well.

Value semantics are explicit:

* a field with no matching element is an empty list;
* an empty leaf element has the value ``''``;
* an element that has only child elements has ``None`` because it has no
  direct text;
* mixed content returns the selected element's direct text only; text nested
  in descendants and tail text after child elements is not included;
* a missing attribute has the value ``None``.

The ``source_path`` always starts at the OAI document root and uses Clark
notation, which makes it independent of the prefixes used by the source
document.
