import json

from legendary.services.desktop import ServiceError

MAX_LINE_BYTES = 1024 * 1024
METHODS = {
    'system.hello': {},
    'auth.status': {},
    'auth.login': {'code': str},
    'auth.logout': {},
    'library.installed': {},
    'library.list': {'platform': str, 'forceRefresh': bool},
    'game.launch': {'appName': str, 'offline': bool, 'skipVersionCheck': bool},
    'install.options': {'appName': str, 'platform': str},
    'install.prepare': {'appName': str, 'platform': str, 'installPath': str},
    'import.prepare': {'appName': str, 'platform': str, 'installPath': str},
    'install.status': {},
    'install.start': {'taskId': str},
    'install.cancel': {'taskId': str},
}
REQUIRED = {
    'auth.login': {'code'}, 'game.launch': {'appName'},
    'install.options': {'appName', 'platform'},
    'install.prepare': {'appName', 'platform', 'installPath'},
    'import.prepare': {'appName', 'platform', 'installPath'},
    'install.start': {'taskId'}, 'install.cancel': {'taskId'},
}


def decode_request(line: str) -> dict:
    try:
        request = json.loads(line)
    except ValueError as exc:
        raise ServiceError('INVALID_REQUEST', 'Expected a JSON object.') from exc
    if not isinstance(request, dict):
        raise ServiceError('INVALID_REQUEST', 'Expected a JSON object.')
    request_id = request.get('id')
    if (
        type(request_id) not in (str, int)
        or isinstance(request_id, str)
        and not 0 < len(request_id) <= 128
    ):
        raise ServiceError(
            'INVALID_REQUEST', 'A string or integer request id is required.'
        )
    return request


def validate_request(request: dict) -> tuple[str, dict]:
    method = request.get('method')
    if not isinstance(method, str) or method not in METHODS:
        raise ServiceError('METHOD_NOT_FOUND', 'Unknown desktop method.')
    params = request.get('params', {})
    schema = METHODS[method]
    if (
        not isinstance(params, dict)
        or params.keys() - schema.keys()
        or REQUIRED.get(method, set()) - params.keys()
    ):
        raise ServiceError('INVALID_PARAMS', 'Unexpected or missing parameters.')
    for key, value in params.items():
        if (
            type(value) is not schema[key]
            or isinstance(value, str)
            and (not value.strip() or len(value) > 16384)
        ):
            raise ServiceError('INVALID_PARAMS', f'Invalid parameter: {key}.')
    if 'platform' in params and params['platform'] not in ('Windows', 'Win32', 'Mac'):
        raise ServiceError('INVALID_PARAMS', 'Unsupported game platform.')
    return method, params
