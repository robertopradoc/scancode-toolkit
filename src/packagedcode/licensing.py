#
# Copyright (c) nexB Inc. and others. All rights reserved.
# ScanCode is a trademark of nexB Inc.
# SPDX-License-Identifier: Apache-2.0
# See http://www.apache.org/licenses/LICENSE-2.0 for the license text.
# See https://github.com/nexB/scancode-toolkit for support or download.
# See https://aboutcode.org for more information about nexB OSS projects.
#

import logging
import os

from license_expression import Licensing

from licensedcode.cache import build_spdx_license_expression
from licensedcode.cache import get_cache
from licensedcode.detection import LicenseDetection
from licensedcode.detection import DetectionCategory
from licensedcode.detection import group_matches
from licensedcode.detection import get_detected_license_expression
from licensedcode.detection import get_matches_from_detections
from licensedcode.detection import get_matches_from_detection_mappings
from licensedcode.detection import get_unknown_license_detection
from licensedcode.detection import get_referenced_filenames
from licensedcode.detection import find_referenced_resource
from licensedcode.detection import detect_licenses
from licensedcode.spans import Span
from licensedcode import query

from packagedcode.utils import combine_expressions
from summarycode.classify import check_resource_name_start_and_end
from summarycode.classify import LEGAL_STARTS_ENDS
from summarycode.classify import README_STARTS_ENDS


"""
Detect and normalize licenses as found in package manifests data.
"""

TRACE = os.environ.get('SCANCODE_DEBUG_PACKAGE_LICENSE', False)


def logger_debug(*args):
    pass


logger = logging.getLogger(__name__)

if TRACE:
    import sys
    logging.basicConfig(stream=sys.stdout)
    logger.setLevel(logging.DEBUG)

    def logger_debug(*args):
        return logger.debug(' '.join(isinstance(a, str) and a or repr(a) for a in args))


def add_referenced_license_matches_for_package(resource, codebase, no_licenses):
    """
    Return an updated ``resource`` saving it in place, after adding new license
    matches (licenses and license_expressions) following their Rule
    ``referenced_filenames`` if any. Return None if ``resource`` is not a file
    Resource or was not updated.
    """
    if TRACE:
        logger_debug(f'packagedcode.licensing: add_referenced_license_matches_for_package: resource: {resource.path}')

    if not resource.is_file:
        return

    package_data = resource.package_data
    if not package_data:
        return

    for pkg in package_data:
    
        license_detections = pkg["license_detections"]
        if not license_detections:
            continue

        modified = False

        for detection in license_detections:
            detection_modified = False
            matches = detection["matches"]
            referenced_filenames = get_referenced_filenames(matches)
            if not referenced_filenames:
                continue 
            
            for referenced_filename in referenced_filenames:
                referenced_resource = find_referenced_resource(
                    referenced_filename=referenced_filename,
                    resource=resource,
                    codebase=codebase,
                )

                if not referenced_resource:
                    continue

                if no_licenses:
                    referenced_license_detections = get_license_detection_mappings(location=referenced_resource.location)
                else:
                    referenced_license_detections = referenced_resource.license_detections

                if referenced_license_detections:
                    modified = True
                    detection_modified = True
                    matches.extend(
                        get_matches_from_detection_mappings(
                            license_detections=referenced_license_detections
                        )
                    )

            if not detection_modified:
                continue

            reasons, license_expression = get_detected_license_expression(
                matches=matches,
                analysis=DetectionCategory.PACKAGE_UNKNOWN_FILE_REFERENCE_LOCAL.value,
                post_scan=True,
            )
            detection["license_expression"] = str(license_expression)
            detection["detection_rules"] = reasons

        if modified:
            license_expressions = [
                detection["license_expression"]
                for detection in license_detections
            ]

            pkg["declared_license_expression"] = combine_expressions(
                expressions=license_expressions,
                relation='AND',
                unique=True,
            )

            pkg["declared_license_expression_spdx"] = str(build_spdx_license_expression(
                license_expression=pkg["declared_license_expression"],
                licensing=get_cache().licensing,
            ))

            codebase.save_resource(resource)
            yield resource


def add_license_from_sibling_file(resource, codebase, no_licenses):
    
    if TRACE:
        logger_debug(f'packagedcode.licensing: add_license_from_sibling_file: resource: {resource.path}')

    if not resource.is_file:
        return

    package_data = resource.package_data
    if not package_data:
        return

    for pkg in package_data:
        pkg_license_detections = pkg["license_detections"]
        if pkg_license_detections:
            return

    license_detections, license_expression = get_license_detections_from_sibling_file(resource, codebase, no_licenses)
    if not license_detections:
        return

    package = resource.package_data[0]
    package["license_detections"] = license_detections
    package["declared_license_expression"] = license_expression
    package["declared_license_expression_spdx"] = str(build_spdx_license_expression(
        license_expression=pkg["declared_license_expression"],
        licensing=get_cache().licensing,
    ))

    codebase.save_resource(resource)
    return package


def get_license_detections_from_sibling_file(resource, codebase, no_licenses):

    siblings = []

    if resource.has_parent():
        for sibling in resource.siblings(codebase):
            is_legal = check_resource_name_start_and_end(resource=sibling, STARTS_ENDS=LEGAL_STARTS_ENDS)
            is_readme = check_resource_name_start_and_end(resource=sibling, STARTS_ENDS=README_STARTS_ENDS)
            if is_legal or is_readme:
                siblings.append(sibling)

    if not siblings:
        return [], None

    license_detections = []
    for sibling in siblings:
        if no_licenses:
            detections = get_license_detection_mappings(
                location=sibling.location,
                analysis=DetectionCategory.PACKAGE_ADD_FROM_SIBLING_FILE.value,
                post_scan=True,
            )
            for detection in detections:
                license_detections.append(detection)
        else:
            license_detections.extend(sibling.license_detections)

    if not license_detections:
        return [], None

    license_expression = get_license_expression_from_detection_mappings(license_detections) 
    return license_detections, license_expression


def get_license_detection_mappings(
    location,
    index=None,
    analysis=None,
    post_scan=False,
):
    license_detections = []
    detections = detect_licenses(
        index=index,
        location=location,
        analysis=analysis,
        post_scan=post_scan
    )

    for detection in detections:
        if detection.license_expression is None:
            continue

        license_detections.append(
            detection.to_dict(
                include_text=True,
                license_text_diagnostics=False,
            )
        )

    return license_detections


def get_declared_license_expression_spdx(declared_license_expression):

    if not declared_license_expression:
        return

    detected_license_expression_spdx = build_spdx_license_expression(
        declared_license_expression,
        licensing=get_cache().licensing,
    )

    return str(detected_license_expression_spdx)


def get_license_matches(location=None, query_string=None):
    """
    Returns a sequence of LicenseMatch objects from license detection of the
    `query_string` or the file at `location`.
    """
    if TRACE:
        logger_debug('get_license_matches: location:', location)

    if not query_string and not location:
        return []

    from licensedcode import cache

    idx = cache.get_index()
    matches = idx.match(location=location, query_string=query_string)

    if TRACE:
        logger_debug('get_license_matches: matches:', matches)

    return matches


def get_license_matches_from_query_string(query_string, start_line=1):
    """
    Returns a sequence of LicenseMatch objects from license detection of the
    `query_string` starting at ``start_line`` number. This is useful when
    matching a text fragment alone when it is part of a larger text.
    """
    if not query_string:
        return []
    from licensedcode import cache

    idx = cache.get_index()
    qry = query.build_query(
        query_string=query_string,
        idx=idx,
        start_line=start_line,
    )

    return idx.match_query(qry=qry)


def get_license_expression_from_matches(license_matches, relation='AND', unique=True):
    """
    Craft a license expression from a list of LicenseMatch objects.
    """
    if not license_matches:
        return

    license_expressions = [
        match.rule.license_expression for match in license_matches
    ]

    if len(license_expressions) == 1:
        license_expression = str(license_expressions[0])
    else:
        license_expression = str(
            combine_expressions(license_expressions, relation=relation, unique=unique)
        )

    return license_expression


def get_license_expression_from_detection_mappings(detections, relation='AND', unique=True):

    expressions = []
    for detection in detections:
        expressions.append(detection["license_expression"])
    
    return str(
        combine_expressions(expressions, relation=relation, unique=unique)
    )


def matches_have_unknown(matches, licensing=Licensing()):
    """
    Return True if any of the LicenseMatch in `matches` has an unknown license.
    """
    for match in matches:
        exp = match.rule.license_expression_object
        if any(
            key in ('unknown', 'unknown-spdx')
            for key in licensing.license_keys(exp)
        ):
            return True


def get_license_detections_from_matches(matches):

    license_detections = []

    if not matches:
        return license_detections

    for group_of_matches in group_matches(matches):
        license_detections.append(
            LicenseDetection.from_matches(group_of_matches)
        )

    return license_detections


def get_license_expression_from_detections(license_detections, relation='AND', unique=True):

    if not license_detections:
        return

    return get_license_expression_from_matches(
        license_matches=get_matches_from_detections(license_detections),
        relation=relation,
        unique=unique,
    )


def get_mapping_and_expression_from_detections(
    license_detections,
    relation='AND',
    unique=True,
    include_text=True,
    license_text_diagnostics=False,
):
    detection_data = []

    if not license_detections:
        return detection_data, None

    license_expression = get_license_expression_from_detections(
        license_detections=license_detections,
        relation=relation,
        unique=unique,
    )

    for license_detection in license_detections:
        detection_data.append(
            license_detection.to_dict(
                include_text=include_text,
                license_text_diagnostics=license_text_diagnostics,
            )
        )

    return detection_data, license_expression


def is_declared_license_not_fully_matched(matches):
    """
    verify that we consumed 100% of the query string e.g. that we
    have no unknown leftover.

    # 1. have all matches 100% coverage?
    # 2. is declared license fully matched?
    """
    all_matches_have_full_coverage = all(m.coverage() == 100 for m in matches)

    query = matches[0].query
    # the query object should be the same for all matches. Is this always true??
    for mt in matches:
        if mt.query != query:
            # FIXME: the expception may be swallowed in callers!!!
            raise Exception(
                'Inconsistent package.extracted_license_statement: text with multiple "queries".'
                'Please report this issue to the scancode-toolkit team.\n'
                f'{matches}'
            )

    query_len = len(query.tokens)
    matched_qspans = [m.qspan for m in matches]
    matched_qpositions = Span.union(*matched_qspans)
    len_all_matches = len(matched_qpositions)
    declared_license_is_fully_matched = query_len == len_all_matches

    if not declared_license_is_fully_matched and not all_matches_have_full_coverage:
        return True
    
    return False


def get_normalized_license_detections(
    extracted_license,
    try_as_expression=True,
    approximate=True,
    expression_symbols=None,
):
    """
    Return a normalized license expression string detected from a list of
    declared license items.
    """
    license_detections = []

    if not extracted_license:
        return license_detections

    if TRACE:
        logger_debug(f'get_normalized_license_detections: extracted_license: {extracted_license}')
        logger_debug(f'get_normalized_license_detections: type(extracted_license): {type(extracted_license)}')

    if not isinstance(extracted_license, list):
        if isinstance(extracted_license, str):
            license_detections = get_license_detections_for_extracted_license_statement(
                extracted_license_statement=extracted_license,
                try_as_expression=try_as_expression,
                approximate=approximate,
                expression_symbols=expression_symbols,
            )
            if TRACE:
                logger_debug(f'get_normalized_license_detections: str:')

        elif isinstance(extracted_license, dict):
            for extracted_license_statement in extracted_license.values():
                detections = get_license_detections_for_extracted_license_statement(
                    extracted_license_statement=extracted_license_statement,
                    try_as_expression=try_as_expression,
                    approximate=approximate,
                    expression_symbols=expression_symbols,
                )
                if TRACE:
                    logger_debug(f'get_normalized_license_detections: dict: extracted_license_statement: {extracted_license_statement}: detections: {detections}')

                if detections:
                    license_detections.extend(detections)

        else:
            extracted_license_statement = repr(extracted_license)
            license_detections = get_license_detections_for_extracted_license_statement(
                extracted_license_statement=extracted_license_statement,
                try_as_expression=try_as_expression,
                approximate=approximate,
                expression_symbols=expression_symbols,
            )
            if TRACE:
                logger_debug(f'get_normalized_license_detections: dict:')
    
    elif isinstance(extracted_license, list):

        for extracted_license_item in extracted_license:
            if isinstance(extracted_license_item, str):
                detections = get_license_detections_for_extracted_license_statement(
                    extracted_license_statement=extracted_license_item,
                    try_as_expression=try_as_expression,
                    approximate=approximate,
                    expression_symbols=expression_symbols,
                )

                if detections:
                    license_detections.extend(detections)

                if TRACE:
                    logger_debug(f'get_normalized_license_detections: list(str): extracted_license_item: {extracted_license_item}: detections: {license_detections}')

            elif isinstance(extracted_license_item, dict):
                for extracted_license_statement in extracted_license_item.values():
                    detections = get_license_detections_for_extracted_license_statement(
                        extracted_license_statement=extracted_license_statement,
                        try_as_expression=try_as_expression,
                        approximate=approximate,
                        expression_symbols=expression_symbols,
                    )
                    if TRACE:
                        logger_debug(f'get_normalized_license_detections: list(dict): extracted_license_statement: {extracted_license_statement}: detections: {detections}')

                    if detections:
                        license_detections.extend(detections)
            
            else:
                extracted_license_statement = repr(extracted_license_item)
                
                detections = get_license_detections_for_extracted_license_statement(
                    extracted_license_statement=extracted_license_statement,
                    try_as_expression=try_as_expression,
                    approximate=approximate,
                    expression_symbols=expression_symbols,
                )

                if detections:
                    license_detections.extend(detections)

                if TRACE:
                    logger_debug(f'get_normalized_license_detections: list(other): extracted_license_statement: {extracted_license_statement}: detections: {license_detections}')

    return license_detections


def get_license_detections_and_expression(
    extracted_license_statement,
    try_as_expression=True,
    approximate=True,
    expression_symbols=None,
):
    """
    Given a text `extracted_license_statement` return a list of LicenseDetection objects.
    `extracted_license_statement` is typically found in package manifests.

    If `try_as_expression` is True try first to parse this as a license
    expression using the ``expression_symbols`` mapping of {lowered key:
    LicenseSymbol} if provided. Otherwise use the standard SPDX license symbols.

    If `approximate` is True, also include approximate license detection as
    part of the matching procedure.

    Return None if the `query_string` is empty. Return "unknown" as a license
    expression if there is a `query_string` but nothing was detected.
    """
    detection_data = []
    license_expression = None

    if not extracted_license_statement:
        return detection_data, license_expression

    license_detections = get_normalized_license_detections(
        extracted_license=extracted_license_statement,
        try_as_expression=try_as_expression,
        approximate=approximate,
        expression_symbols=expression_symbols,
    )

    if not license_detections:
        if not isinstance(extracted_license_statement, str):
            extracted_license_statement = repr(extracted_license_statement)
        license_detection = get_unknown_license_detection(extracted_license_statement)
        license_detections = [license_detection]

    return get_mapping_and_expression_from_detections(license_detections)


def get_license_detections_for_extracted_license_statement(
    extracted_license_statement,
    try_as_expression=True,
    approximate=True,
    expression_symbols=None,
):
    if not extracted_license_statement:
        return []
    
    if not isinstance(extracted_license_statement, str):
        extracted_license_statement = repr(extracted_license_statement)

    matches, matched_as_expression = get_license_matches_for_extracted_license_statement(
        query_string=extracted_license_statement,
        try_as_expression=try_as_expression,
        approximate=approximate,
        expression_symbols=expression_symbols,
    )

    if not matches:
        extracted_license_statement = 'license ' + extracted_license_statement
        matches, matched_as_expression = get_license_matches_for_extracted_license_statement(
            query_string=extracted_license_statement,
            try_as_expression=False,
            approximate=approximate,
            expression_symbols=expression_symbols,
        )

        if not matches:
            return []

    license_detections = get_license_detections_from_matches(matches) 

    if matched_as_expression:
        return license_detections

    if is_declared_license_not_fully_matched(matches):
        # FIXME: Only include the undetected part in the matched_text
        license_detections.append(
            get_unknown_license_detection(extracted_license_statement)
        )

    return license_detections


def get_license_matches_for_extracted_license_statement(
    query_string,
    try_as_expression=True,
    approximate=True,
    expression_symbols=None,
):
    """
    Return `matches` list of LicenseMatch and a flag `matched_as_expression`
    which is True if the `query_string` was matched as an expression.

    Here the `query_string` is generally a extracted_license_statement
    which can be a valid license-expression.
    """
    from licensedcode.cache import get_index
    idx = get_index()

    # we match twice in a cascade: as an expression, then as plain text if we
    # did not succeed.
    matches = None
    if try_as_expression:
        try:
            matched_as_expression = True
            matches = idx.match(
                query_string=query_string,
                as_expression=True,
                expression_symbols=expression_symbols,
            )
            if matches_have_unknown(matches):
                # rematch also if we have unknowns
                matched_as_expression = False
                matches = idx.match(
                    query_string=query_string,
                    as_expression=False,
                    approximate=approximate,
                )

        except Exception:
            matched_as_expression = False
            matches = idx.match(
                query_string=query_string,
                as_expression=False,
                approximate=approximate,
            )
    else:
        matched_as_expression = False
        matches = idx.match(
            query_string=query_string,
            as_expression=False,
            approximate=approximate,
        )

    return matches, matched_as_expression


def get_normalized_expression(
    query_string,
    try_as_expression=True,
    approximate=True,
    expression_symbols=None,
    licensing=Licensing(),
):
    """
    Given a text `query_string` return a single detected license expression.
    `query_string` is typically the value of a license field as found in package
    manifests.

    If `try_as_expression` is True try first to parse this as a license
    expression using the ``expression_symbols`` mapping of {lowered key:
    LicenseSymbol} if provided. Otherwise use the standard SPDX license symbols.

    If `approximate` is True, also include approximate license detection as
    part of the matching procedure.

    Return None if the `query_string` is empty. Return "unknown" as a license
    expression if there is a `query_string` but nothing was detected.
    """
    if not query_string or not query_string.strip():
        return

    if TRACE:
        logger_debug(f'get_normalized_expression: query_string: "{query_string}"')

    matches, matched_as_expression = get_license_matches_for_extracted_license_statement(
        query_string, try_as_expression, approximate, expression_symbols,
    )

    if not matches:
        # we have a query_string text but there was no match: return an unknown
        # key
        return 'unknown'

    if TRACE:
        logger_debug('get_normalized_expression: matches:', matches)

    # join the possible multiple detected license expression with an AND
    expression_objects = [m.rule.license_expression_object for m in matches]
    if len(expression_objects) == 1:
        combined_expression_object = expression_objects[0]
    else:
        combined_expression_object = licensing.AND(*expression_objects)

    if matched_as_expression:
        # then just return the expression(s)
        return str(combined_expression_object)

    # Otherwise, verify that we consumed 100% of the query string e.g. that we
    # have no unknown leftover.

    # 1. have all matches 100% coverage?
    all_matches_have_full_coverage = all(m.coverage() == 100 for m in matches)

    # TODO: have all matches a high enough score?

    # 2. are all declared license tokens consumed?
    query = matches[0].query
    # the query object should be the same for all matches. Is this always true??
    for mt in matches:
        if mt.query != query:
            # FIXME: the expception may be swallowed in callers!!!
            raise Exception(
                'Inconsistent package.declared_license: text with multiple "queries".'
                'Please report this issue to the scancode-toolkit team.\n'
                f'{query_string}'
            )

    query_len = len(query.tokens)
    matched_qspans = [m.qspan for m in matches]
    matched_qpositions = Span.union(*matched_qspans)
    len_all_matches = len(matched_qpositions)
    declared_license_is_fully_matched = query_len == len_all_matches

    if not all_matches_have_full_coverage or not declared_license_is_fully_matched:
        # We inject an 'unknown' symbol in the expression
        unknown = licensing.parse('unknown', simple=True)
        combined_expression_object = licensing.AND(combined_expression_object, unknown)

    return str(combined_expression_object)
