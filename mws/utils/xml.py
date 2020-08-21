"""Utilities for working with XML documents, particularly those
that are returned in a majority of MWS responses.
"""

import re

import xmltodict

from mws.utils.collections import DotDict

# from mws.utils import DotDict
MWS_ENCODING = "iso-8859-1"
"""Amazon documents this as the encoding to expect in responses.
If it doesn't work, :shrug:, we'll have to wing it.
"""


def remove_xml_namespaces(data):
    """Return namespaces found in the XML data."""
    # pattern = re.compile(r'xmlns[:ns2]*="\S+"')
    pattern = re.compile(r'xmlns(:ns2)?="[^"]+"|(ns2:)|(xml:)')
    return pattern.sub("", data)


def mws_xml_to_dict(data, encoding=MWS_ENCODING, force_cdata=False, **kwargs):
    """Convert XML expected from MWS to a Python dict.
    Extracts namespaces and passes data into `xmltodict.parse`
    with some useful defaults:
        force_cdata
    """
    # Extracted namespaces
    data = remove_xml_namespaces(data)
    # Run the parser
    xmldict = xmltodict.parse(
        data,
        encoding=encoding,
        process_namespaces=False,
        dict_constructor=dict,
        # namespaces=namespaces,
        force_cdata=force_cdata,
    )
    # Return the results of the first key (?), otherwise the original
    finaldict = xmldict.get(list(xmldict.keys())[0], xmldict)
    return finaldict


def mws_xml_to_dotdict(data, result_key=None, force_cdata=False):
    """Convert XML expected from MWS to a DotDict object.
    first using `mws_xml_to_dict` for our default args to `xmltodict.parse`
    and then sending the res
    """
    xmldict = mws_xml_to_dict(data, force_cdata=force_cdata)
    if result_key:
        xmldict = xmldict.get(result_key, xmldict)
    return DotDict(xmldict)