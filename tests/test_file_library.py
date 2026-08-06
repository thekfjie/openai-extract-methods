import json
import tempfile
import unittest
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from unittest.mock import patch

import server
from integrations import file_library


class FileLibraryTests(unittest.TestCase):
    def test_create_list_update_and_delete_preserve_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            content = "first line\r\nsecond line\r\n"
            created = file_library.create_file("applemail.txt", content, root)

            self.assertEqual(created["content"], content)
            self.assertEqual(created["sizeBytes"], len(content.encode("utf-8")))
            self.assertEqual(file_library.list_files(root)[0]["name"], "applemail.txt")
            self.assertNotIn("content", file_library.list_files(root)[0])

            updated = file_library.update_file(
                created["id"], name="applemail-notes.md", content=content + "tail", root=root
            )
            self.assertEqual(file_library.get_file(created["id"], root)["content"], content + "tail")
            self.assertEqual(updated["name"], "applemail-notes.md")

            deleted = file_library.delete_file(created["id"], root)
            self.assertEqual(deleted["name"], "applemail-notes.md")
            self.assertEqual(file_library.list_files(root), [])
            with self.assertRaises(file_library.FileLibraryError) as missing:
                file_library.get_file(created["id"], root)
            self.assertEqual(missing.exception.status_code, 404)

    def test_rejects_duplicate_unsafe_and_non_text_names(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            file_library.create_file("notes.txt", "one", root)
            with self.assertRaises(file_library.FileLibraryError) as duplicate:
                file_library.create_file("NOTES.TXT", "two", root)
            self.assertEqual(duplicate.exception.status_code, 409)
            for invalid_name in ("../notes.txt", "folder/notes.txt", "archive.zip"):
                with self.subTest(invalid_name=invalid_name):
                    with self.assertRaises(file_library.FileLibraryError):
                        file_library.create_file(invalid_name, "text", root)

    def test_authenticated_http_routes_form_a_complete_management_cycle(self) -> None:
        original_password = server.CONFIG.admin_password
        with tempfile.TemporaryDirectory() as temporary_directory, patch.object(
            file_library, "FILE_LIBRARY_DIR", Path(temporary_directory)
        ):
            server.CONFIG.admin_password = ""
            httpd = server.AutomyaiHTTPServer(("127.0.0.1", 0), server.AppHandler)
            thread = Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                connection = HTTPConnection("127.0.0.1", httpd.server_port, timeout=2)
                payload = json.dumps({"name": "sample.txt", "content": "copy me\r\n"})
                connection.request("POST", "/api/file-library", payload, {"Content-Type": "application/json"})
                response = connection.getresponse()
                self.assertEqual(response.status, 201)
                item = json.loads(response.read())["item"]

                connection.request("GET", f"/api/file-library/{item['id']}")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read())["item"]["content"], "copy me\r\n")

                connection.request("DELETE", f"/api/file-library/{item['id']}")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertTrue(json.loads(response.read())["deleted"])
                connection.close()
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)
                server.CONFIG.admin_password = original_password


if __name__ == "__main__":
    unittest.main()
