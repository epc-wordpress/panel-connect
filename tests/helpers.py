import httpx

NS = 'xmlns="http://api.namecheap.com/xml.response"'


def nc_ok(command: str, body: str) -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<ApiResponse Status="OK" {NS}><Errors />'
            f'<CommandResponse Type="{command}">{body}</CommandResponse></ApiResponse>')


def nc_error(message: str) -> str:
    return (f'<?xml version="1.0" encoding="utf-8"?>'
            f'<ApiResponse Status="ERROR" {NS}><Errors>'
            f'<Error Number="500000">{message}</Error></Errors></ApiResponse>')


def mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))
