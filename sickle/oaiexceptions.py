# coding: utf-8
"""
    sickle.oaiexceptions
    ~~~~~~~~~~~~~~~~~~~~

    OAI errors and errors raised by the resumable consumption interface.

    :copyright: Copyright 2015 Mathias Loesch
"""


class BadArgument(Exception):
    """
    The request includes illegal arguments, is missing required arguments,
    includes a repeated argument, or values for arguments have an illegal
    syntax.
    """
    pass


class BadVerb(Exception):
    """
    Value of the verb argument is not a legal OAI-PMH verb, the verb argument
    is missing, or the verb argument is repeated.
    """
    pass


class BadResumptionToken(Exception):
    """
    The value of the resumptionToken argument is invalid or expired.
    """
    pass


class CannotDisseminateFormat(Exception):
    """
    The metadata format identified by the value given for the metadataPrefix
    argument is not supported by the item or by the repository.
    """
    pass


class IdDoesNotExist(Exception):
    """
    The value of the identifier argument is unknown or illegal in this
    repository.
    """
    pass


class NoSetHierarchy(Exception):
    """
    The repository does not support sets.
    """
    pass


class NoMetadataFormat(Exception):
    """
    There are no metadata formats available for the specified item.
    """
    pass


class NoRecordsMatch(Exception):
    """
    The combination of the values of the from, until, set and metadataPrefix
    arguments results in an empty list.
    """
    pass


class OAIError(Exception):
    """
    Context specific OAI errors not covered by the classes above.
    """
    pass


class CheckpointError(Exception):
    """Base class for errors raised while resuming a consumption session."""
    pass


class CheckpointConflict(CheckpointError):
    """A checkpoint cannot be resumed because the remote data has changed.

    Checkpoints deliberately do not fall back to positional indexes: if a
    page that has been downloaded before now contains a different sequence of
    records, the only safe behaviour is to stop and let the caller decide what
    to do with the already delivered records.
    """


class CheckpointMismatch(CheckpointError):
    """A checkpoint is not compatible with the requested harvest.

    This is raised when the endpoint, the OAI arguments, the metadata format
    or the OAI verb differ from the values recorded in the checkpoint.
    """


class IncompletePage(CheckpointError):
    """A checkpoint that has not acknowledged a whole page cannot be resumed
    from the next page.

    The next resumption token is only progress *after* every record of the
    current page has been acknowledged; otherwise unacknowledged records would
    silently be skipped.
    """


class UnknownRecord(CheckpointError):
    """A record presented to :meth:`ack` is not part of the active page."""
