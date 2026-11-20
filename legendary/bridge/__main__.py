"""Run with `python -m legendary.bridge`; stdout is exclusively IPC."""

import json
import logging
import sys
from contextlib import redirect_stdout, suppress
from multiprocessing import freeze_support

from requests.exceptions import RequestException, SSLError

from legendary import __version__
from legendary.bridge import PROTOCOL_VERSION
from legendary.bridge.protocol import (
    MAX_LINE_BYTES,
    METHODS,
    decode_request,
    validate_request,
)
from legendary.services.desktop import DesktopService, ServiceError


def serve(source, output, service):
    while line := source.readline(MAX_LINE_BYTES + 1):
        request_id = None
        try:
            if len(line.encode('utf-8')) > MAX_LINE_BYTES:
                # Drain the rest of an oversized message without treating it as requests.
                while not line.endswith('\n'):
                    line = source.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        break
                raise ServiceError('INVALID_REQUEST', 'Request is too large.')
            request = decode_request(line)
            request_id = request['id']
            method, params = validate_request(request)
            with redirect_stdout(sys.stderr):
                result = (
                    {
                        'protocolVersion': PROTOCOL_VERSION,
                        'version': __version__,
                        'methods': list(METHODS),
                    }
                    if method == 'system.hello'
                    else service.call(method, params)
                )
            response = {'id': request_id, 'result': result}
        except ServiceError as exc:
            response = {
                'id': request_id,
                'error': {'code': exc.code, 'message': str(exc)},
            }
        except SSLError:
            response = {
                'id': request_id,
                'error': {
                    'code': 'TLS_ERROR',
                    'message': 'Could not verify the secure connection to Epic Games.',
                },
            }
        except RequestException:
            response = {
                'id': request_id,
                'error': {
                    'code': 'NETWORK_ERROR',
                    'message': 'Could not reach Epic Games. Check your connection.',
                },
            }
        except Exception as exc:
            # Exception values may contain authorization codes, tokens or launch arguments.
            logging.getLogger('DesktopBridge').error(
                'Request failed (%s).', type(exc).__name__
            )
            response = {
                'id': request_id,
                'error': {
                    'code': 'INTERNAL_ERROR',
                    'message': 'The operation could not be completed.',
                },
            }
        output.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + '\n')
        output.flush()


def main():
    freeze_support()
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format='[%(name)s] %(levelname)s: %(message)s',
    )
    service = DesktopService()
    try:
        with suppress(BrokenPipeError, KeyboardInterrupt):
            serve(sys.stdin, sys.stdout, service)
    finally:
        service.close()


if __name__ == '__main__':
    main()
