import socket
import ssl
import unittest
from unittest.mock import patch
from urllib import error
import network
import setup_app

class NetworkTests(unittest.TestCase):
    def test_verified_tls(self):
        context=network.tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED)

    def test_dns_and_certificate_have_distinct_safe_messages(self):
        url='https://api.telegram.org/botVERY_SECRET/getMe'
        dns=network.connection_message(url,error.URLError(socket.gaierror(8,'VERY_SECRET')))
        tls=network.connection_message(url,error.URLError(ssl.SSLCertVerificationError(1,'VERY_SECRET')))
        self.assertIn('DNS',dns)
        self.assertIn('TLS_CERTIFICATE',tls)
        self.assertNotIn('VERY_SECRET',dns+tls)
        self.assertIn('api.telegram.org',tls)

    def test_api_preserves_specific_failure(self):
        with patch.object(setup_app.request,'build_opener') as opener:
            opener.return_value.open.side_effect=error.URLError(socket.gaierror(8,'private token'))
            with self.assertRaisesRegex(setup_app.SetupError,'DNS'):
                setup_app.api('https://api.telegram.org/botprivate/getMe')

    def test_timeout_message(self):
        self.assertIn('TIMEOUT',network.connection_message('https://api.github.com',TimeoutError()))
