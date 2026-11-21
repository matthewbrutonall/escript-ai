import unittest

from ai.errors import MESSAGES, classify, public_message


class Named(Exception): pass


def error(name, message=''):
    return type(name, (Exception,), {})(message)


class ErrorClassificationTests(unittest.TestCase):
    def test_named_errors(self):
        cases = {
            ('RemoteAIForbidden', 'could not be checked'): 'policy_unreadable',
            ('RemoteAIForbidden', 'refused'): 'offsite_forbidden',
            ('APIKeyUnavailable', ''): 'key_unreadable',
            ('BudgetExceeded', ''): 'budget',
            ('CrossDocumentError', ''): 'wrong_document',
            ('Timeout', ''): 'provider_timeout',
            ('ReadTimeout', ''): 'provider_timeout',
            ('ConnectTimeout', ''): 'provider_timeout',
            ('ConnectionError', ''): 'provider_unreachable',
            ('SSLError', ''): 'provider_unreachable',
        }
        for (name, message), expected in cases.items():
            with self.subTest(name=name, message=message): self.assertEqual(classify(error(name, message)), expected)

    def test_http_status_and_message_prefixes(self):
        class Response: status_code = 401
        exc = RuntimeError('hostile')
        exc.response = Response()
        self.assertEqual(classify(exc), 'key_rejected')
        for status, expected in ((403, 'key_rejected'), (429, 'provider_quota'), (400, 'provider_rejected'), (404, 'provider_rejected'), (422, 'provider_rejected'), (500, 'provider_down'), (599, 'provider_down')):
            exc.response.status_code = status
            with self.subTest(status=status): self.assertEqual(classify(exc), expected)
        for prefix, expected in (('Anthropic 401: bad', 'key_rejected'), ('OpenAI 429 nope', 'provider_quota'), ('chat completions 400 nope', 'provider_rejected'), ('Gemini 503 nope', 'provider_down')):
            self.assertEqual(classify(RuntimeError(prefix)), expected)

    def test_fixed_message_fallback_and_suffix(self):
        self.assertEqual(classify(RuntimeError('No API key resolved for x')), 'no_api_key')
        self.assertEqual(classify(RuntimeError('provider requires an API key')), 'no_api_key')
        self.assertEqual(classify(RuntimeError('provider redirect refused')), 'provider_unreachable')
        self.assertEqual(public_message('missing'), MESSAGES['unexpected'])
        self.assertTrue(public_message('unexpected', page_index=3, page_total=20).endswith('Stopped at page 3 of 20; earlier pages were processed and the layer is marked for review.'))

    def test_hostile_text_is_never_public(self):
        result = public_message(classify(RuntimeError('<img src=x onerror=alert(1)> key_ref=SECRET')))
        self.assertNotIn('<', result)
        self.assertNotIn('SECRET', result)
