# Licensed to the StackStorm, Inc ('StackStorm') under one or more
# contributor license agreements.  See the NOTICE file distributed with
# this work for additional information regarding copyright ownership.
# The ASF licenses this file to You under the Apache License, Version 2.0
# (the "License"); you may not use this file except in compliance with
# the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import re
import sys
from sre_parse import (
    parse, AT, AT_BEGINNING, AT_BEGINNING_STRING, AT_END, AT_END_STRING,
    BRANCH, SUBPATTERN,
)

from st2common.exceptions.content import ParseException
from st2common import log

__all__ = [
    'ActionAliasFormatParser',

    'extract_parameters_for_action_alias_db',
    'extract_parameters',
    'search_regex_tokens',
]


LOG = log.getLogger(__name__)

# Python 3 compatibility
if sys.version_info > (3,):
    SUBPATTERN_INDEX = 3
else:
    SUBPATTERN_INDEX = 1


class ActionAliasFormatParser(object):

    def __init__(self, alias_format=None, param_stream=None):
        self._format = alias_format or ''
        self._param_stream = param_stream or ''

    def get_extracted_param_value(self):
        """
        Match command against the format string and extract paramters from the command string.

        :rtype: ``dict``
        """
        result = {}

        param_stream = self._param_stream

        # As there's a lot of questions about using regular expressions,
        # I'll try to be thorough when documenting this code.

        # I'll split the whole convoluted regex into snippets to make it
        # a bit more readable (hopefully).
        snippets = dict()

        # Formats for keys and values: key is a non-spaced string,
        # value is anything in quotes or curly braces, or a single word.
        snippets['key'] = r'\s*(\S+?)\s*'
        snippets['value'] = r'""|\'\'|"(.+?)"|\'(.+?)\'|({.+?})|(\S+)'

        # Extended value: also matches unquoted text (caution).
        snippets['ext_value'] = r'""|\'\'|"(.+?)"|\'(.+?)\'|({.+?})|(.+?)'

        # Key-value pair:
        snippets['pairs'] = r'(?:^|\s+){key}=({value})'.format(**snippets)

        # End of string: multiple space-separated key-value pairs:
        snippets['ending'] = r'.*?(({pairs}\s*)*)$'.format(**snippets)

        # Default value in optional parameters:
        snippets['default'] = r'\s*=\s*(?:{ext_value})\s*'.format(**snippets)

        # Optional parameter (has a default value):
        snippets['optional'] = '{{' + snippets['key'] + snippets['default'] + '}}'

        # Required parameter (no default value):
        snippets['required'] = '{{' + snippets['key'] + '}}'

        # 1. Matching the arbitrary key-value pairs at the end of the command
        # to support extra parameters (not specified in the format string),
        # and cutting them from the command string afterwards.
        ending_pairs = re.match(snippets['ending'], param_stream, re.DOTALL)
        has_ending_pairs = ending_pairs and ending_pairs.group(1)
        if has_ending_pairs:
            kv_pairs = re.findall(snippets['pairs'], ending_pairs.group(1), re.DOTALL)
            param_stream = param_stream.replace(ending_pairs.group(1), '')
        param_stream = " %s " % (param_stream)

        # 2. Matching optional parameters (with default values).
        optional = re.findall(snippets['optional'], self._format, re.DOTALL)

        # Transforming our format string into a regular expression,
        # substituting {{ ... }} with regex named groups, so that param_stream
        # matched against this expression yields a dict of params with values.
        param_match = r'\1["\']?(?P<\2>(?:(?<=\').+?(?=\')|(?<=").+?(?=")|{.+?}|.+?))["\']?'
        reg = re.sub(r'(\s*)' + snippets['optional'], r'(?:' + param_match + r')?', self._format)
        reg = re.sub(r'(\s*)' + snippets['required'], param_match, reg)

        reg_tokens = parse(reg, flags=re.DOTALL)

        # Add a beginning anchor if none exists
        if not search_regex_tokens(((AT, AT_BEGINNING), (AT, AT_BEGINNING_STRING)), reg_tokens):
            reg = r'^\s*' + reg

        # Add an ending anchor if none exists
        if not search_regex_tokens(((AT, AT_END), (AT, AT_END_STRING)), reg_tokens, backwards=True):
            reg = reg + r'\s*$'

        # 3. Matching the command against our regex to get the param values
        matched_stream = re.match(reg, param_stream, re.DOTALL)

        if not matched_stream:
            # If no match is found we throw since this indicates provided user string (command)
            # didn't match the provided format string
            raise ParseException('Command "%s" doesn\'t match format string "%s"' %
                                 (self._param_stream, self._format))

        # Compiling results from the steps 1-3.
        if matched_stream:
            result = matched_stream.groupdict()

        for param in optional:
            matched_value = result[param[0]] if matched_stream else None
            matched_result = matched_value or ''.join(param[1:])
            if matched_result is not None:
                result[param[0]] = matched_result

        if has_ending_pairs:
            for pair in kv_pairs:
                result[pair[0]] = ''.join(pair[2:])

        if self._format and not (self._param_stream.strip() or any(result.values())):
            raise ParseException('No value supplied and no default value found.')

        return result


def extract_parameters_for_action_alias_db(action_alias_db, format_str, param_stream):
    """
    Extract parameters from the user input based on the provided format string.

    Note: This function makes sure that the provided format string is indeed available in the
    action_alias_db.formats.
    """
    formats = []
    formats = action_alias_db.get_format_strings()

    if format_str not in formats:
        raise ValueError('Format string "%s" is not available on the alias "%s"' %
                         (format_str, action_alias_db.name))

    result = extract_parameters(format_str=format_str, param_stream=param_stream)
    return result


def extract_parameters(format_str, param_stream):
    parser = ActionAliasFormatParser(alias_format=format_str, param_stream=param_stream)
    return parser.get_extracted_param_value()


def search_regex_tokens(needle_tokens, haystack_tokens, backwards=False):
    """
    Search a tokenized regex for any tokens in needle_tokens. Returns True if
    any token tuple in needle_tokens is found, and False otherwise.

    >>> search_regex_tokens(((AT, AT_END), (AT, AT_END)), parse(r'^asdf'))
    False

    :param needle_tokens: an iterable of token tuples

    >>> needle_tokens = ((AT, AT_END), (AT, AT_END))
    >>> search_regex_tokens(needle_tokens, parse(r'^asdf$'))
    True

    :param haystack_tokens: an iterable of token tuples from sre_parse.parse

    >>> regex_tokens = parse(r'^(?:more regex)$')
    >>> list(regex_tokens)  # doctest: +NORMALIZE_WHITESPACE
    [(AT, AT_BEGINNING),
     (SUBPATTERN, (None, 0, 0,
     [(LITERAL, 109), (LITERAL, 111), (LITERAL, 114), (LITERAL, 101),
      (LITERAL, 32), (LITERAL, 114), (LITERAL, 101), (LITERAL, 103),
      (LITERAL, 101), (LITERAL, 120)])), (AT, AT_END)]

    >>> search_regex_tokens(((AT, AT_END), (AT, AT_END)), regex_tokens)
    True

    :param backwards: Controls direction of search, defaults to False.
    :type backwards: bool or None

    .. note:: Set backwards to True if needle_tokens are more likely to be
    found at the end of the haystack_tokens iterable, eg: ending anchors.

    >>> search_regex_tokens(((AT, AT_END), (AT, AT_END)), parse(r'^asdf$'))
    True
    >>> search_regex_tokens(((AT, AT_END), (AT, AT_END)), parse(r'^asdf$'), backwards=True)
    True

    :rtype: ``bool``
    """
    if backwards:
        haystack_tokens = reversed(haystack_tokens)

    for rtoken_type, rtoken in haystack_tokens:
        LOG.debug("Matching: ({}, {})".format(rtoken_type, rtoken))
        if rtoken_type == SUBPATTERN:
            LOG.debug("SUBPATTERN: {}".format(rtoken))
            if search_regex_tokens(needle_tokens, rtoken[SUBPATTERN_INDEX]):
                return True
        elif rtoken_type == BRANCH:
            LOG.debug("BRANCH: {}".format(rtoken))
            if search_regex_tokens(needle_tokens, rtoken[1][1]):
                return True
        elif (rtoken_type, rtoken) in needle_tokens:
            LOG.debug("Found: {}".format((rtoken_type, rtoken)))
            return True
    else:
        LOG.debug("Not found: {}".format(needle_tokens))
        return False
