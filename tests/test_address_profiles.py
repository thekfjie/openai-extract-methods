from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from integrations import address_profiles


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self._data = json.dumps(payload).encode("utf-8")
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._data):
            return b""
        if size < 0:
            size = len(self._data) - self._offset
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


class AddressProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        address_profiles._LAST_REQUEST_BY_CLIENT.clear()

    def test_catalog_includes_requested_and_future_country_set(self) -> None:
        codes = {item["code"] for item in address_profiles.address_country_catalog()}
        self.assertTrue({"JP", "BR", "US", "GB", "TR", "CA", "AU", "DE", "VN"}.issubset(codes))
        self.assertEqual(len(codes), 23)

    @patch("integrations.address_profiles.urlopen")
    def test_us_tax_free_address_reads_matching_source_pool(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse({
            "country": "US",
            "region": "OR",
            "generatedAt": "2026-06-24T14:31:56.033Z",
            "cities": [{
                "name": "Portland",
                "weight": 1,
                "streets": [{
                    "name": "North Test Avenue",
                    "weight": 1,
                    "postcodes": [{"value": "97201", "weight": 1}],
                    "houseNumbers": {"numeric": [{"value": 120, "weight": 1}], "numericAlpha": []},
                }],
            }],
        })

        result = address_profiles.fetch_us_tax_free_address("OR", client_key="tax-free-fixture")

        self.assertEqual(result["address"]["formatted"], "120 North Test Avenue, Portland, OR 97201, US")
        self.assertEqual(result["source"]["provider"], "usaddressgen.com")
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("country=US&region=OR", request.full_url)
        self.assertEqual(request.get_header("Origin"), "https://usaddressgen.com")

    @patch("integrations.address_profiles.urlopen")
    def test_fetch_forwards_all_upstream_fields_without_filtering(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse(
            {
                "status": "ok",
                "address": {
                    "Full_Name": "Remote Test Person",
                    "Address": "10 Example Road",
                    "City": "London",
                    "State": "England",
                    "Zip_Code": "SW1A 1AA",
                    "Occupation": "Tester",
                    "Credit_Card_Type": "blocked-brand",
                    "Credit_Card_Number": "blocked-number",
                    "CVV2": "blocked-cvv",
                    "Expires": "blocked-expiry",
                    "Social_Security_Number": "blocked-id",
                    "Unknown_Future_Field": "must-not-pass-default-deny",
                },
            }
        )

        result = address_profiles.fetch_address_profile("GB", "London", client_key="test-passthrough")

        self.assertEqual(result["fields"]["Full_Name"], "Remote Test Person")
        self.assertEqual(result["fields"]["Occupation"], "Tester")
        self.assertEqual(result["fields"]["Credit_Card_Type"], "blocked-brand")
        self.assertEqual(result["fields"]["Credit_Card_Number"], "blocked-number")
        self.assertEqual(result["fields"]["CVV2"], "blocked-cvv")
        self.assertEqual(result["fields"]["Expires"], "blocked-expiry")
        self.assertEqual(result["fields"]["Social_Security_Number"], "blocked-id")
        self.assertEqual(result["fields"]["Unknown_Future_Field"], "must-not-pass-default-deny")
        self.assertNotIn("blockedFields", result)
        self.assertNotIn("blockedPolicy", result)

        request = mocked_urlopen.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request.full_url, address_profiles.MEIGUO_ADDRESS_ENDPOINT)
        self.assertEqual(request_body, {"city": "London", "path": "/uk-address", "method": "refresh"})

    @patch("integrations.address_profiles.urlopen")
    def test_blank_city_uses_country_random_address_method(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse(
            {"status": "ok", "address": {"Address": "1 Test Street", "City": "Tokyo", "Zip_Code": "100-0001"}}
        )
        result = address_profiles.fetch_address_profile("JP", "", client_key="test-random-city")
        request = mocked_urlopen.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_body, {"city": "", "path": "/jp-address", "method": "address"})
        self.assertEqual(result["country"]["code"], "JP")

    @patch("integrations.address_profiles.urlopen")
    def test_brazil_html_source_is_normalized(self, mocked_urlopen) -> None:
        html = b'''<div class="panel" id="address-box"><div class="result-box">
          <dd><label>\xe5\x85\xa8\xe5\x90\x8d</label><span><b>Maria Teste</b></span></dd>
          <dd><label>\xe8\xa1\x97\xe9\x81\x93</label><span><b>Rua Exemplo 10</b></span></dd>
          <dd><label>\xe5\x9f\x8e\xe5\xb8\x82</label><span><b>Sao Paulo</b></span></dd>
          <dd><label>\xe9\x82\xae\xe7\xbc\x96</label><span><b>01000-000</b></span></dd>
        </div><div class="panel-footer">footer</div></div>'''
        response = FakeResponse({})
        response._data = html
        mocked_urlopen.return_value = response

        result = address_profiles.fetch_address_profile("BR", "", client_key="test-brazil")

        self.assertEqual(result["source"]["provider"], "cn.americaaddress.com")
        self.assertEqual(result["fields"]["Full_Name"], "Maria Teste")
        self.assertEqual(result["fields"]["Address"], "Rua Exemplo 10")

    def test_invalid_country_and_long_city_are_rejected_without_network(self) -> None:
        with self.assertRaises(address_profiles.AddressProfileError) as country_error:
            address_profiles.fetch_address_profile("XX", "", client_key="test-invalid-country")
        self.assertEqual(country_error.exception.status_code, 400)

        with self.assertRaises(address_profiles.AddressProfileError) as city_error:
            address_profiles.fetch_address_profile("US", "x" * 81, client_key="test-invalid-city")
        self.assertEqual(city_error.exception.status_code, 400)

    @patch("integrations.address_profiles.urlopen")
    def test_per_client_rate_limit_protects_upstream(self, mocked_urlopen) -> None:
        mocked_urlopen.return_value = FakeResponse(
            {"status": "ok", "address": {"Address": "1 Test Street", "City": "New York", "Zip_Code": "10001"}}
        )
        address_profiles.fetch_address_profile("US", "", client_key="same-client")
        with self.assertRaises(address_profiles.AddressProfileError) as error:
            address_profiles.fetch_address_profile("US", "", client_key="same-client")
        self.assertEqual(error.exception.status_code, 429)
        self.assertEqual(mocked_urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
