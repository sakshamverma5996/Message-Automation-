import unittest

from group_adder import WhatsAppGroupEngine, format_phone_number, get_contact_field


class GroupAddHelpersTests(unittest.TestCase):
    def test_phone_number_normalization(self):
        self.assertEqual(format_phone_number("98765 43210", "+91"), "919876543210")
        self.assertEqual(format_phone_number("+44 (20) 1234-5678", "+91"), "442012345678")
        self.assertEqual(format_phone_number("#ERROR!", "+91"), "")

    def test_contact_field_aliases(self):
        contact = {"Phone Number": "9876543210", "Full Name": "Asha"}
        self.assertEqual(
            get_contact_field(contact, ["phone", "mobile", "number"]), "9876543210"
        )
        self.assertEqual(get_contact_field(contact, ["name"]), "Asha")

    def test_engine_requires_per_contact_confirmation(self):
        engine = WhatsAppGroupEngine()
        started, reason = engine.start_group_add(
            "Campus Group", [{"phone": "9876543210"}], {}
        )
        self.assertFalse(started)
        self.assertIn("Per-contact confirmation", reason)
        self.assertFalse(engine.is_running)


if __name__ == "__main__":
    unittest.main()
