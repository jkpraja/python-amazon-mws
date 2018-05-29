# -*- coding: utf-8 -*-
"""
Main module for python-amazon-mws package.
"""

from __future__ import absolute_import

import base64
import datetime
import hashlib
import hmac
import re
import warnings
from zipfile import ZipFile
from io import BytesIO
import xmltodict

from requests import request
from requests.exceptions import HTTPError

from . import utils

try:
    from urllib.parse import quote
except ImportError:
    from urllib import quote
from xml.parsers.expat import ExpatError


__version__ = '1.0.0dev0'


# See https://images-na.ssl-images-amazon.com/images/G/01/mwsportal/doc/en_US/bde/MWSDeveloperGuide._V357736853_.pdf
# page 8
# for a list of the end points and marketplace IDs

MARKETPLACES = {
    "CA": "https://mws.amazonservices.ca",  # A2EUQ1WTGCTBG2
    "US": "https://mws.amazonservices.com",  # ATVPDKIKX0DER",
    "DE": "https://mws-eu.amazonservices.com",  # A1PA6795UKMFR9
    "ES": "https://mws-eu.amazonservices.com",  # A1RKKUPIHCS9HS
    "FR": "https://mws-eu.amazonservices.com",  # A13V1IB3VIYZZH
    "IN": "https://mws.amazonservices.in",  # A21TJRUUN4KGV
    "IT": "https://mws-eu.amazonservices.com",  # APJ6JRA9NG5V4
    "UK": "https://mws-eu.amazonservices.com",  # A1F83G8C2ARO7P
    "JP": "https://mws.amazonservices.jp",  # A1VC38T7YXB528
    "CN": "https://mws.amazonservices.com.cn",  # AAHKV2X7AFYLW
    "MX": "https://mws.amazonservices.com.mx",  # A1AM78C64UM0Y8
}


class MWSError(Exception):
    """
    Main MWS Exception class
    """
    # Allows quick access to the response object.
    # Do not rely on this attribute, always check if its not None.
    response = None


def calc_request_description(params):
    """
    Returns a flatted string with the request description, built from the params dict.

    Keys should appear in alphabetical order in the result string.
    Example:
      params = {'foo': 1, 'bar': 4, 'baz': 'potato'}
    Returns:
      "bar=4&baz=potato&foo=1"
    """
    description_items = [
        '{}={}'.format(item, params[item]) for item in sorted(params.keys())]
    return '&'.join(description_items)


def clean_params(params):
    """Input cleanup, html-escape and prevent a lot of common input mistakes."""
    # silently remove parameter where values are empty
    params = {k: v for k, v in params.items() if v}

    params_enc = dict()
    for key, value in params.items():
        if isinstance(value, (dict, list, set, tuple)):
            message = 'expected string or datetime datatype, got {},'\
                'for key {} and value {}'.format(
                    type(value), key, str(value))
            raise MWSError(message)
        if isinstance(value, (datetime.datetime, datetime.date)):
            value = value.isoformat()
        if isinstance(value, bool):
            value = str(value).lower()
        value = str(value)

        params_enc[key] = quote(value, safe='-_.~')
    return params_enc


def validate_hash(response):
    hash_ = utils.calc_md5(response.content)
    if response.headers['content-md5'].encode() != hash_:
        raise MWSError("Wrong Content length, maybe amazon error...")


class DataWrapper(object):
    """Main class that handles all responses."""

    def __init__(self, data, rootkey=None):
        """Easy access for nicely processed response objets."""
        self.original = data
        self.headers = self.original.headers
        self.pydict = None

        self._rootkey = rootkey
        self._response_dict = None
        self.main()

    def main(self):
        """Try different parsing strategies."""
        # a better guess for the correct encoding
        self.original.encoding = self.original.apparent_encoding
        textdata = self.original.text
        # We don't trust the amazon content marker.
        try:
            self.xml2dict(textdata)
        except ExpatError:
            self.textdata = textdata

    def xml2dict(self, rawdata):
        """Parse XML with xmltodict."""
        namespaces = self.extract_namespaces(rawdata)
        self._mydict = xmltodict.parse(rawdata, dict_constructor=dict,
                                       process_namespaces=True,
                                       namespaces=namespaces)
        # unpack if possible, important for accessing the rootkey
        self.pydict = self._mydict.get(list(self._mydict.keys())[0], self._mydict)
        self._response_dict = utils.DotDict(self.pydict)

    def extract_namespaces(self, rawdata):
        """Parse all namespaces."""
        pattern = re.compile(r'xmlns[:ns2]*="\S+"')
        raw_namespaces = pattern.findall(rawdata)
        return {x.split('"')[1]: None for x in raw_namespaces}

    @property
    def parsed(self):
        """Recieve a nice formatted response, this can be your default."""
        if self._response_dict is not None:
            # when we parsed succesful a xml response
            return self._response_dict.get(self._rootkey, self._response_dict)
        else:
            # when it is plain text
            return self.textdata

    """
    To return an unzipped file object based on the content type"
    """
    @property
    def unzipped(self):
        """
        If the response is comprised of a zip file, returns a ZipFile object of those file contents.
        Otherwise, returns None.
        """
        if self.headers['content-type'] == 'application/zip':
            try:
                with ZipFile(BytesIO(self.original)) as unzipped_fileobj:
                    # unzipped the zip file contents
                    unzipped_fileobj.extractall()
                    # return original zip file object to the user
                    return unzipped_fileobj
            except Exception as exc:
                raise MWSError(str(exc))
        return None  # 'The response is not a zipped file.'


class MWS(object):
    """
    Base Amazon API class
    """
    # This is used to post/get to the different uris used by amazon per api
    # ie. /Orders/2011-01-01
    # All subclasses must define their own URI only if needed
    URI = "/"

    # The API version varies in most amazon APIs
    VERSION = "2009-01-01"

    # In here we name each of the operations available to the subclass
    # that have 'ByNextToken' operations associated with them.
    # If the Operation is not listed here, self.action_by_next_token
    # will raise an error.
    NEXT_TOKEN_OPERATIONS = []

    # Some APIs are available only to either a "Merchant" or "Seller"
    # the type of account needs to be sent in every call to the amazon MWS.
    # This constant defines the exact name of the parameter Amazon expects
    # for the specific API being used.
    # All subclasses need to define this if they require another account type
    # like "Merchant" in which case you define it like so.
    # ACCOUNT_TYPE = "Merchant"
    # Which is the name of the parameter for that specific account type.

    # For using proxy you need to init this class with one more parameter proxies. It must look like 'ip_address:port'
    # if proxy without auth and 'login:password@ip_address:port' if proxy with auth

    ACCOUNT_TYPE = "SellerId"

    def __init__(self, access_key, secret_key, account_id,
                 region='US', domain='', uri="",
                 version="", auth_token="", proxy=None):
        self.access_key = access_key
        self.secret_key = secret_key
        self.account_id = account_id
        self.auth_token = auth_token
        self.version = version or self.VERSION
        self.uri = uri or self.URI
        self.proxy = proxy

        # * TESTING FLAGS * #
        self._test_request_params = False

        if domain:
            # TODO test needed to enter here.
            self.domain = domain
        elif region in MARKETPLACES:
            self.domain = MARKETPLACES[region]
        else:
            # TODO test needed to enter here.
            error_msg = "Incorrect region supplied ('{region}'). Must be one of the following: {marketplaces}".format(
                marketplaces=', '.join(MARKETPLACES.keys()),
                region=region,
            )
            raise MWSError(error_msg)

    def get_default_params(self):
        """
        Get the parameters required in all MWS requests
        """
        params = {
            'AWSAccessKeyId': self.access_key,
            self.ACCOUNT_TYPE: self.account_id,
            'SignatureVersion': '2',
            'Timestamp': utils.get_utc_timestamp(),
            'Version': self.version,
            'SignatureMethod': 'HmacSHA256',
        }
        if self.auth_token:
            params['MWSAuthToken'] = self.auth_token
        # TODO current tests only check for auth_token being set.
        # need a branch test to check for auth_token being skipped (no key present)
        return params

    def make_request(self, extra_data, method="GET", **kwargs):
        """
        Make request to Amazon MWS API with these parameters
        """
        params = self.get_default_params()
        proxies = self.get_proxies()
        params.update(extra_data)
        params = clean_params(params)
        # rootkey is always the Action parameter from your request function,
        # except for get_feed_submission_result
        rootkey = kwargs.get('rootkey', extra_data.get("Action") + "Result")

        if self._test_request_params:
            # Testing method: return the params from this request before the request is made.
            return params
        # TODO: All current testing stops here. More branches needed.

        request_description = calc_request_description(params)
        signature = self.calc_signature(method, request_description)
        url = "{domain}{uri}?{description}&Signature={signature}".format(
            domain=self.domain,
            uri=self.uri,
            description=request_description,
            signature=quote(signature),
        )
        headers = {'User-Agent': 'python-amazon-mws/{} (Language=Python)'.format(__version__)}
        headers.update(kwargs.get('extra_headers', {}))

        try:
            # The parameters are included in the url string.
            response = request(method, url, data=kwargs.get(
                'body', ''), headers=headers, proxies=proxies)
            response.raise_for_status()

            if 'content-md5' in response.headers:
                validate_hash(response)
            parsed_response = DataWrapper(response, rootkey)

        except HTTPError as exc:
            error = MWSError(str(exc.response.text))
            error.response = exc.response
            raise error

        return parsed_response

    def get_proxies(self):
        proxies = {"http": None, "https": None}
        if self.proxy:
            # TODO need test to enter here
            proxies = {
                "http": "http://{}".format(self.proxy),
                "https": "https://{}".format(self.proxy),
            }
        return proxies

    def get_service_status(self):
        """
        Returns a GREEN, GREEN_I, YELLOW or RED status.
        Depending on the status/availability of the API its being called from.
        """
        return self.make_request(extra_data=dict(Action='GetServiceStatus'))

    def action_by_next_token(self, action, next_token):
        """
        Run a '...ByNextToken' action for the given action.
        If the action is not listed in self.NEXT_TOKEN_OPERATIONS, MWSError is raised.
        Action is expected NOT to include 'ByNextToken'
        at the end of its name for this call: function will add that by itself.
        """
        if action not in self.NEXT_TOKEN_OPERATIONS:
            # TODO Would like a test entering here.
            # Requires a dummy API class to be written that will trigger it.
            raise MWSError((
                "{} action not listed in this API's NEXT_TOKEN_OPERATIONS. "
                "Please refer to documentation."
            ).format(action))

        action = '{}ByNextToken'.format(action)

        data = {
            'Action': action,
            'NextToken': next_token,
        }
        return self.make_request(data, method="POST")

    def calc_signature(self, method, request_description):
        """
        Calculate MWS signature to interface with Amazon
        Args:
            method (str)
            request_description (str)
        """
        sig_data = '\n'.join([
            method,
            self.domain.replace('https://', '').lower(),
            self.uri,
            request_description
        ])
        return base64.b64encode(hmac.new(self.secret_key.encode(), sig_data.encode(), hashlib.sha256).digest())

    def enumerate_param(self, param, values):
        """
        DEPRECATED.
        Please use `utils.enumerate_param` for one param, or
        `utils.enumerate_params` for multiple params.
        """
        # TODO remove in 1.0 release.
        # No tests needed.
        warnings.warn((
            "Please use `utils.enumerate_param` for one param, or "
            "`utils.enumerate_params` for multiple params."
        ), DeprecationWarning)
        return utils.enumerate_param(param, values)