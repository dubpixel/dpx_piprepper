# ================================================================================
# TESTS - cardbless web GUI
# ================================================================================
# PROJECT: cardbless (dpx_piPrepper)
# File: test_gui.py
# Purpose: checks validation, boot-volume detection, a bless into a temp
#          directory, and the server's auth rules. Never touches a real disk.
# Run: python3 -m unittest tests/test_gui.py
# ================================================================================

import importlib.util
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
spec = importlib.util.spec_from_file_location("cardbless_gui", SRC / "cardbless-gui.py")
gui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gui)

GOOD = {
    "username": "dubpixel", "hostname": "compoperator", "password": "pw", "password2": "pw",
    "ip": "10.196.11.25", "prefix": "15", "gateway": "10.196.11.1", "vlans": [],
}


class Validation(unittest.TestCase):
    def test_good_fields_pass(self):
        f, errs = gui.clean_bless_fields(GOOD)
        self.assertEqual(errs, [])
        self.assertEqual(f["net"]["ip"], "10.196.11.25")

    def test_companion_user_rejected(self):
        _, errs = gui.clean_bless_fields({**GOOD, "username": "companion"})
        self.assertTrue(errs)

    def test_bad_hostname_and_password_mismatch(self):
        _, errs = gui.clean_bless_fields({**GOOD, "hostname": "-bad", "password2": "x"})
        self.assertEqual(len(errs), 2)

    def test_ip_outside_subnet_rejected(self):
        _, errs = gui.clean_bless_fields({**GOOD, "gateway": "192.168.1.1"})
        self.assertTrue(errs)

    def test_vlan_linklocal_and_static(self):
        f, errs = gui.clean_bless_fields({**GOOD, "vlans": [{"id": "218", "mode": "linklocal"}]})
        self.assertEqual((errs, f["net"]["vlan"]), ([], [{"id": 218, "ip": None, "prefix": None}]))
        f, errs = gui.clean_bless_fields({**GOOD, "vlans": [{"id": "206", "mode": "static", "ip": "10.206.11.25", "prefix": "15"}]})
        self.assertEqual(errs, [])
        self.assertEqual(f["net"]["vlan"][0]["ip"], "10.206.11.25")

    def test_three_vlans_ok_four_rejected(self):
        three = [{"id": "218", "mode": "linklocal"},
                 {"id": "206", "mode": "static", "ip": "10.206.11.25", "prefix": "15"},
                 {"id": "300", "mode": "static", "ip": "10.40.0.5", "prefix": "24"}]
        f, errs = gui.clean_bless_fields({**GOOD, "vlans": three})
        self.assertEqual((errs, len(f["net"]["vlan"])), ([], 3))
        _, errs = gui.clean_bless_fields({**GOOD, "vlans": three + [{"id": "400", "mode": "linklocal"}]})
        self.assertTrue(errs)

    def test_duplicate_id_and_overlaps_rejected(self):
        _, errs = gui.clean_bless_fields({**GOOD, "vlans": [{"id": "218", "mode": "linklocal"}, {"id": "218", "mode": "linklocal"}]})
        self.assertTrue(errs)
        _, errs = gui.clean_bless_fields({**GOOD, "vlans": [{"id": "206", "mode": "static", "ip": "10.197.1.1", "prefix": "15"}]})
        self.assertTrue(errs)   # overlaps main
        two = [{"id": "206", "mode": "static", "ip": "10.40.0.5", "prefix": "24"},
               {"id": "207", "mode": "static", "ip": "10.40.0.9", "prefix": "24"}]
        _, errs = gui.clean_bless_fields({**GOOD, "vlans": two})
        self.assertTrue(errs)   # overlap each other
        _, errs = gui.clean_bless_fields({**GOOD, "vlans": [{"id": "5000", "mode": "linklocal"}]})
        self.assertTrue(errs)

    def test_render_multi_vlan_and_single_unchanged(self):
        one = {"id": 218, "ip": None, "prefix": None}
        two = {"id": 206, "ip": "10.206.11.25", "prefix": 15}
        r = gui.core.render_network_config
        self.assertEqual(r("10.0.0.2", 24, "10.0.0.1", one), r("10.0.0.2", 24, "10.0.0.1", [one]))
        text = r("10.0.0.2", 24, "10.0.0.1", [one, two])
        self.assertEqual(text.count("  vlans:\n"), 1)
        self.assertIn("eth0.218", text)
        self.assertIn("eth0.206", text)
        gui.core.yaml.safe_load(text)

class BootAndBless(unittest.TestCase):
    def make_boot(self, root: Path, name: str) -> Path:
        d = root / name
        d.mkdir()
        for m in gui.core.BOOT_MARKERS:
            (d / m).write_text("x")
        return d

    def test_only_real_boot_volumes_listed(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            self.make_boot(root, "bootfs")
            (root / "Macintosh HD").mkdir()
            names = [v["name"] for v in gui.list_boot_volumes(root)]
            self.assertEqual(names, ["bootfs"])

    def test_bless_writes_validated_files(self):
        with tempfile.TemporaryDirectory() as t:
            boot = self.make_boot(Path(t), "bootfs")
            f, errs = gui.clean_bless_fields({**GOOD, "vlans": [{"id": "218", "mode": "linklocal"}]})
            self.assertEqual(errs, [])
            gui.core.LOG_FILE = Path(t) / "test.log"  # keep the real log clean
            msg = gui.do_bless(boot, f, "$6$fakehash", str(boot), eject_after=False)
            self.assertIn("blessed", msg)
            self.assertIn("link-local: [ipv4]", (boot / "network-config").read_text())
            self.assertIn("compoperator", (boot / "user-data").read_text())
            self.assertIn("OK", gui.core.LOG_FILE.read_text())


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = gui.ThreadingHTTPServer(("127.0.0.1", 0), gui.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, path, body=None, token=None, host=None):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data)
        if token is not None:
            r.add_header("X-Token", token)
        if host:
            r.add_header("Host", host)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_page_serves_with_token(self):
        code, body = self.req("/")
        self.assertEqual(code, 200)
        self.assertIn(gui.TOKEN.encode(), body)

    def test_post_without_token_forbidden(self):
        self.assertEqual(self.req("/api/bless", GOOD)[0], 403)
        self.assertEqual(self.req("/api/bless", GOOD, token="wrong")[0], 403)

    def test_foreign_host_forbidden(self):
        self.assertEqual(self.req("/", host="evil.example")[0], 403)

    def test_bless_rejects_unlisted_volume(self):
        code, _ = self.req("/api/bless", {**GOOD, "boot": "/etc"}, token=gui.TOKEN)
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main()
