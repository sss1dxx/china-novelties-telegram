"""Verified HTTPS and credential-safe diagnostics for macOS Python installs."""
import socket
import ssl
from urllib import error, parse, request


def tls_context():
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        try:
            from pip._vendor import certifi
        except ImportError:
            certifi = None
    if certifi is not None:
        context.load_verify_locations(cafile=certifi.where())
    # Keep certificate verification and hostname checks enabled.
    return context


def https_handler():
    return request.HTTPSHandler(context=tls_context())


def connection_message(url, exc):
    host = parse.urlsplit(url).hostname or 'сервис'
    reason = exc.reason if isinstance(exc, error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        detail = 'Не удалось проверить SSL-сертификат. Перезапустите обновлённый START.command для установки сертификатов; если ошибка останется, проверьте HTTPS-фильтрацию антивируса или прокси.'
        code = 'TLS_CERTIFICATE'
    elif isinstance(reason, socket.gaierror):
        detail = 'DNS не смог определить адрес сервера. Проверьте системное интернет-подключение и DNS/VPN: расширение VPN только в браузере не подключает Python.'
        code = 'DNS'
    elif isinstance(reason, (TimeoutError, socket.timeout)):
        detail = 'Сервер не ответил вовремя. Проверьте доступность сервиса через системное подключение/VPN и повторите.'
        code = 'TIMEOUT'
    elif isinstance(reason, ConnectionRefusedError):
        detail = 'Соединение отклонено. Проверьте системный прокси и сетевой фильтр.'
        code = 'CONNECTION_REFUSED'
    elif isinstance(reason, ssl.SSLError):
        detail = 'Не удалось установить защищённое соединение. Проверьте настройки системного прокси/VPN.'
        code = 'TLS'
    else:
        detail = 'Сетевой запрос не выполнен. Проверьте системный интернет и доступность сервиса.'
        code = 'NETWORK'
    # Never include str(exc): urllib exceptions can contain bot tokens in URLs.
    return f'{host} · {code}: {detail}'
