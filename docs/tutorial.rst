========
Tutorial
========

This section gives a brief overview on how to use Sickle for querying OAI
interfaces.


Initialize an OAI Interface
===========================

To make a connection to an OAI interface, you need to import the Sickle object::

    >>> from sickle import Sickle

Next, you can initialize the connection by passing it the basic URL. In our
example, we use the OAI interface of the ELIS repository::

    >>> sickle = Sickle('http://elis.da.ulcc.ac.uk/cgi/oai2')


Issuing Requests
================

Sickle provides methods for each of the six OAI verbs (ListRecords, GetRecord,
Idenitfy, ListSets, ListMetadataFormats, ListIdentifiers). Start with a
ListRecords request::

    >>> records = sickle.ListRecords(metadataPrefix='oai_dc')

Note that all keyword arguments you provide to this function are passed to the OAI interface
as HTTP parameters. Therefore the example request would send the parameters
``verb=ListRecords&metadataPrefix=oai_dc``.
We can add additional parameters, like, for example, an OAI ``set``::

    >>> records = sickle.ListRecords(metadataPrefix='oai_dc', set='driver')

Consecutive Harvesting
======================

Since most OAI verbs yield more than one element, their respective Sickle methods
return iterator objects which can be used to iterate over the records of a
repository::

    >>> records = sickle.ListRecords(metadataPrefix='oai_dc')
    >>> records.next()
    <Record oai:eprints.rclis.org:4088>

Note that this works with all verbs that return more than one element.
These are: :meth:`~sickle.app.Sickle.ListRecords`, :meth:`~sickle.app.Sickle.ListIdentifiers`,
:meth:`~sickle.app.Sickle.ListSets`, and :meth:`~sickle.app.Sickle.ListMetadataFormats`.

The following example shows how to iterate over the headers returned by ``ListIdentifiers``::

    >>> headers = sickle.ListIdentifiers(metadataPrefix='oai_dc')
    >>> headers.next()
    <Header oai:eprints.rclis.org:4088>

Iterating over the the sets returned by ``ListSets`` works similarly::

    >>> sets = sickle.ListSets()
    >>> sets.next()
    <Set Status = In Press>


Using the ``from`` Parameter
============================

If you need to perform selective harvesting by date using the ``from`` parameter, you
may face the problem that ``from`` is a reserved word in Python::

    >>> records = sickle.ListRecords(metadataPrefix='oai_dc', from="2012-12-12")
      File "<stdin>", line 1
        records = sickle.ListRecords(metadataPrefix='oai_dc', from="2012-12-12")
                                                                  ^
    SyntaxError: invalid syntax

Fortunately, you can circumvent this problem by using a dictionary together with
the ``**`` operator::

    >>> records = sickle.ListRecords(
    ...             **{'metadataPrefix': 'oai_dc',
    ...             'from': '2012-12-12'
    ...            })


Getting a Single Record
=======================

OAI-PMH allows you to get a single record by using the ``GetRecord`` verb::

    >>> sickle.GetRecord(identifier='oai:eprints.rclis.org:4088',
    ...                  metadataPrefix='oai_dc')
    <Record oai:eprints.rclis.org:4088>


Two-Phase Harvesting (ListIdentifiers + GetRecord)
==================================================

If you want to select the items to harvest from ``ListIdentifiers`` first and
fetch their metadata with ``GetRecord`` afterwards, the
:func:`sickle.harvest.harvest` function chains both requests into one
pipeline::

    >>> from sickle.harvest import harvest
    >>> results = harvest(sickle, metadataPrefix='oai_dc')

Each result keeps the header as listed next to the header actually returned by
``GetRecord`` (``listed_header`` and ``actual_header``) so that data changes
happening between the two requests are visible instead of silently merged:

* ``unchanged``: both headers agree;
* ``updated``: the record's datestamp changed, so the newer metadata must not
  be stored under the listed (older) datestamp;
* ``deleted``: the record was deleted between the two requests;
* ``failed``: the record could not be retrieved; previous results are kept and
  the exception is attached to the result.

What happens with ``updated`` and ``deleted`` items is decided by the caller
and recorded as an ``action`` on every result. By default newer versions are
accepted and deleted items are skipped::

    >>> results = harvest(sickle, on_updated='conflict',
    ...                   on_deleted='accept', metadataPrefix='oai_dc')

With ``on_updated='conflict'`` the newer record is stashed on the result for
later reconciliation instead of being accepted; ``on_deleted='accept'`` keeps
the deletion marker. A single failed ``GetRecord`` never aborts the iteration,
so the already harvested results remain available.


Harvesting OAI Items vs. OAI Responses
======================================

Sickle supports two harvesting modes that differ in the type of the returned
objects. The default mode returns OAI-specific *items* (records, headers etc.)
encoded as Python objects as seen earlier. If you want to save the whole XML
response returned by the server, you have to pass the
:class:`sickle.iterator.OAIResponseIterator` during the instantiation of the
:class:`~sickle.app.Sickle` object::

    >>> sickle = Sickle('http://elis.da.ulcc.ac.uk/cgi/oai2', iterator=OAIResponseIterator)
    >>> responses = Sickle.ListRecords(metadataPrefix='oai_dc')
    >>> responses.next()
    <OAIResponse ListRecords>

You could then save the returned responses to disk::

    >>> with open('response.xml', 'w') as fp:
    ...     fp.write(responses.next().raw.encode('utf8'))



Ignoring Deleted Records
========================

The :meth:`~sickle.app.Sickle.ListRecords` and :meth:`~sickle.app.Sickle.ListIdentifiers`
methods accept an optional parameter :attr:`ignore_deleted`. If set to :obj:`True`,
the returned :class:`~sickle.iterator.OAIItemIterator` will skip deleted records/headers::

    >>> records = sickle.ListRecords(metadataPrefix='oai_dc', ignore_deleted=True)

.. note::

    This works only using the :class:`sickle.iterator.OAIItemIterator`. If you
    use the :class:`sickle.iterator.OAIResponseIterator`, the resulting OAI
    responses will still contain the deleted records.

