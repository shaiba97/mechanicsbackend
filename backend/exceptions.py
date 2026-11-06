from rest_framework.views import exception_handler

CODE_BY_STATUS = {
    400: 'VALIDATION_ERROR',
    401: 'UNAUTHORIZED',
    403: 'FORBIDDEN',
    404: 'NOT_FOUND',
    405: 'METHOD_NOT_ALLOWED',
    409: 'CONFLICT',
    429: 'RATE_LIMITED',
}


def _message_from(details):
    if isinstance(details, str):
        return details
    if isinstance(details, dict):
        for key in ('detail', 'non_field_errors'):
            if key in details:
                return _message_from(details[key])
        for value in details.values():
            message = _message_from(value)
            if message:
                return message
    if isinstance(details, list) and details:
        return _message_from(details[0])
    return 'Request failed'


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    status = response.status_code
    response.data = {
        'error': {
            'code': CODE_BY_STATUS.get(status, f'HTTP_{status}'),
            'message': _message_from(response.data),
            'details': response.data,
        }
    }
    return response
