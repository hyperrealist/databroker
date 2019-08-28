from datetime import datetime
import glob
import os
import sys
import warnings

import doct
import numpy as np
import pytz
import yaml


class ALL:
    "Sentinel used as the default value for stream_name"
    pass


def ensure_path_exists(path, exist_ok=True):
    return os.makedirs(path, exist_ok=exist_ok)


def sanitize_np(val):
    "Convert any numpy objects into built-in Python types."
    if isinstance(val, (np.generic, np.ndarray)):
        if np.isscalar(val):
            return val.item()
        return val.tolist()
    return val


def apply_to_dict_recursively(d, f):
    for key, val in d.items():
        if hasattr(val, 'items'):
            d[key] = apply_to_dict_recursively(val, f)
        d[key] = f(val)


def format_time(search_dict, tz):
    """Helper function to format the time arguments in a search dict

    Expects 'since' and 'until'

    ..warning: Does in-place mutation of the search_dict
    """
    # The old names of 'since' and 'until' are 'start_time' and 'stop_time'.
    if 'since' in search_dict and 'start_time' in search_dict:
        raise TypeError("cannot use both 'since' and its deprecated name "
                        "'start_time'")
    if 'until' in search_dict and 'stop_time' in search_dict:
        raise TypeError("cannot use both 'until' and its deprecated name "
                        "'stop_time'")
    if 'start_time' in search_dict or 'stop_time' in search_dict:
        warnings.warn("The keyword 'start_time' and 'stop_time' have been "
                      "renamed to 'since' and 'until'. The old names are "
                      "deprecated.")
    time_dict = {}
    since = search_dict.pop('since', search_dict.pop('start_time', None))
    until = search_dict.pop('until', search_dict.pop('stop_time', None))
    if since:
        time_dict['$gte'] = normalize_human_friendly_time(since, tz)
    if until:
        time_dict['$lte'] = normalize_human_friendly_time(until, tz)
    if time_dict:
        search_dict['time'] = time_dict


# human friendly timestamp formats we'll parse
_TS_FORMATS = [
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d %H:%M',  # these 2 are not as originally doc'd,
    '%Y-%m-%d %H',     # but match previous pandas behavior
    '%Y-%m-%d',
    '%Y-%m',
    '%Y']

# build a tab indented, '-' bulleted list of supported formats
# to append to the parsing function docstring below
_doc_ts_formats = '\n'.join('\t- {}'.format(_) for _ in _TS_FORMATS)


def normalize_human_friendly_time(val, tz):
    """Given one of :
    - string (in one of the formats below)
    - datetime (eg. datetime.now()), with or without tzinfo)
    - timestamp (eg. time.time())
    return a timestamp (seconds since jan 1 1970 UTC).

    Non string/datetime values are returned unaltered.
    Leading/trailing whitespace is stripped.
    Supported formats:
    {}
    """
    # {} is placeholder for formats; filled in after def...

    zone = pytz.timezone(tz)  # tz as datetime.tzinfo object
    epoch = pytz.UTC.localize(datetime(1970, 1, 1))
    check = True

    if isinstance(val, str):
        # unix 'date' cmd format '%a %b %d %H:%M:%S %Z %Y' works but
        # doesn't get TZ?

        # Could cleanup input a bit? remove leading/trailing [ :,-]?
        # Yes, leading/trailing whitespace to match pandas behavior...
        # Actually, pandas doesn't ignore trailing space, it assumes
        # the *current* month/day if they're missing and there's
        # trailing space, or the month is a single, non zero-padded digit.?!
        val = val.strip()

        for fmt in _TS_FORMATS:
            try:
                ts = datetime.strptime(val, fmt)
                break
            except ValueError:
                pass

        try:
            if isinstance(ts, datetime):
                val = ts
                check = False
            else:
                # what else could the type be here?
                raise TypeError('expected datetime,'
                                ' got {:r}'.format(ts))

        except NameError:
            raise ValueError('failed to parse time: ' + repr(val))

    if check and not isinstance(val, datetime):
        return val

    if val.tzinfo is None:
        # is_dst=None raises NonExistent and Ambiguous TimeErrors
        # when appropriate, same as pandas
        val = zone.localize(val, is_dst=None)

    return (val - epoch).total_seconds()


# fill in the placeholder we left in the previous docstring
normalize_human_friendly_time.__doc__ = (
    normalize_human_friendly_time.__doc__.format(_doc_ts_formats)
)


def get_fields(header, name=None):
    """
    Return the set of all field names (a.k.a "data keys") in a header.

    Parameters
    ----------
    header : Header
    name : string, optional
        Get field from only one "event stream" with this name. If None
        (default) get fields from all event streams.

    Returns
    -------
    fields : set
    """
    fields = set()
    for descriptor in header['descriptors']:
        if name is not None and name != descriptor.get('name', 'primary'):
            continue
        for field in descriptor['data_keys'].keys():
            fields.add(field)
    return fields


DOCT_NAMES = {'resource': 'Resource',
              'datum': 'Datum',
              'descriptor': 'Event Descriptor',
              'event': 'Event',
              'start': 'Run Start',
              'stop': 'Run Stop'}


def wrap_in_doct(name, doc):
    """
    Put document contents into a doct.Document object.

    A ``doct.Document`` is a subclass of dict that:

    * is immutable
    * provides human-readable :meth:`__repr__` and :meth:`__str__`
    * supports dot access (:meth:`__getattr__`) as a synonym for item access
      (:meth:`__getitem__`) whenever possible
    """
    return doct.Document(DOCT_NAMES[name], doc)


_STANDARD_DICT_ATTRS = dir(dict)


class DeprecatedDoct(doct.Document):
    "Subclass of doct.Document that warns that dot access may be removed."
    # We must use __getattribute__ here, not the gentle __getattr__, in order
    # to successfully override doct.Document. doct.Document aggressively edits
    # its own __dict__, a subclass's __getattr__ would never be called.
    def __getattribute__(self, key):
        # Get the result first and let any errors be raised.
        res = super(DeprecatedDoct, self).__getattribute__(key)
        # Now warn before returning it.
        if not (key in _STANDARD_DICT_ATTRS or key.startswith('_')):
            # This is not a standard dict attribute.
            # Warn that dot access is deprecated.
            warnings.warn("Dot access may be removed in a future version. "
                          "Use ['{0}'] instead of .{0}".format(key))
        if key == '_name':
            warnings.warn("In a future version of databroker, plain dicts "
                          "without a '_name' attribute may be returned. "
                          "Do not rely on '_name'.")
        return res


def wrap_in_deprecated_doct(name, doc):
    """
    Put document contents into a DeprecatedDoct object.

    See :func:`wrap_in_doct`. The difference between :class:`DeprecatedDoct`
    and :class:`doct.Document` is a warning that dot access
    (:meth:`__getattr__` as a synonym for :meth:`__getitem__`) may be removed
    in the future.
    """
    return DeprecatedDoct(DOCT_NAMES[name], doc)


class DuplicateHandler(RuntimeError):
    pass


# Search order is (for unix):
#   ~/.config/databroker
#   <sys.executable directory>/../etc/databroker
#   /etc/databroker
# And for Windows we only look in:
#   %APPDATA%/databroker


if os.name == 'nt':
    _user_conf = os.path.join(os.environ['APPDATA'], 'databroker')
    CONFIG_SEARCH_PATH = (_user_conf,)
else:
    _user_conf = os.path.join(os.path.expanduser('~'), '.config', 'databroker')
    _local_etc = os.path.join(os.path.dirname(os.path.dirname(sys.executable)),
                              'etc', 'databroker')
    _system_etc = os.path.join('/', 'etc', 'databroker')
    CONFIG_SEARCH_PATH = (_user_conf, _local_etc, _system_etc)


def list_configs():
    """
    List the names of the available configuration files.

    Returns
    -------
    names : list

    See Also
    --------
    :func:`describe_configs`
    """
    names = set()
    for path in CONFIG_SEARCH_PATH:
        files = glob.glob(os.path.join(path, '*.yml'))
        names.update([os.path.basename(f)[:-4] for f in files])

    # Do not include _legacy_config.
    names.discard(SPECIAL_NAME)

    return sorted(names)


def describe_configs():
    """
    Get the names and descriptions of available configuration files.

    Returns
    -------
    configs : dict
        map names to descriptions (if available)

    See Also
    --------
    :func:`list_configs`
    """
    return {name: lookup_config(name).get('description')
            for name in list_configs()}


def lookup_config(name):
    """
    Search for a databroker configuration file with a given name.

    For exmaple, the name 'example' will cause the function to search for:

    * ``~/.config/databroker/example.yml``
    * ``{python}/../etc/databroker/example.yml``
    * ``/etc/databroker/example.yml``

    where ``{python}`` is the location of the current Python binary, as
    reported by ``sys.executable``. It will use the first match it finds.

    Parameters
    ----------
    name : string

    Returns
    -------
    config : dict
    """
    if not name.endswith('.yml'):
        name += '.yml'
    tried = []
    for path in CONFIG_SEARCH_PATH:
        filename = os.path.join(path, name)
        tried.append(filename)
        if os.path.isfile(filename):
            with open(filename) as f:
                return yaml.load(f,
                                 Loader=getattr(yaml, 'FullLoader',
                                                yaml.Loader)
                                 )
    else:
        raise FileNotFoundError("No config file named {!r} could be found in "
                                "the following locations:\n{}"
                                "".format(name, '\n'.join(tried)))
