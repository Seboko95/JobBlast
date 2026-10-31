import unittest
from datetime import date
from io import BytesIO

from docx import Document

from services.cover_letter_docs import build_cover_letter_filename, export_cover_letter_docx
from services.cover_letter_format import formatted_cover_letter_text


class CoverLetterDocxTests(unittest.TestCase):
    def test_build_cover_letter_filename_sanitizes_values(self):
        filename = build_cover_letter_filename(
            {
                "title": "Field Sales Representative (Healthcare Sector)",
                "company": "ExecutivePlacements.com",
            }
        )

        self.assertTrue(filename.endswith("_cover_letter.docx"))
        self.assertNotIn(" ", filename)
        self.assertIn("Field_Sales_Representative", filename)

    def test_export_cover_letter_docx_creates_readable_document(self):
        filename, payload = export_cover_letter_docx(
            {
                "name": "Test User",
                "address_line": "123 Main Road",
                "city_province_postal": "Johannesburg, Gauteng, 2000",
                "email": "test@example.com",
                "phone": "+27 12 000 0000",
                "linkedin": "linkedin.com/in/test-user",
            },
            {
                "title": "Medical Representative",
                "company": "Example Pharma",
                "location": "Johannesburg",
            },
            {
                "cover_letter": "Paragraph one.\n\nParagraph two.",
                "motivation_summary": "Strong healthcare-facing fit.",
                "suggested_answers": ["Answer one", "Answer two"],
                "cv_alignment_notes": ["Lead with OTC experience."],
            },
        )

        self.assertTrue(filename.endswith(".docx"))
        self.assertGreater(len(payload), 0)

        document = Document(BytesIO(payload))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        self.assertIn("123 Main Road", text)
        self.assertIn("Subject: Application for Medical Representative Position", text)
        self.assertIn("Dear Hiring Manager,", text)
        self.assertIn("Test User", text)
        self.assertIn("Paragraph one.", text)
        self.assertIn("Yours faithfully,", text)

    def test_formatted_cover_letter_text_uses_professional_layout(self):
        text = formatted_cover_letter_text(
            {
                "name": "Test User",
                "address_line": "123 Main Road",
                "city_province_postal": "Johannesburg, Gauteng, 2000",
                "email": "test@example.com",
                "phone": "+27 12 000 0000",
            },
            {
                "title": "Medical Representative",
                "company": "Example Pharma",
                "location": "Johannesburg",
            },
            {
                "cover_letter": "Paragraph one.\n\nParagraph two.\n\nParagraph three.\n\nParagraph four.",
            },
            today=date(2026, 4, 27),
        )

        self.assertIn("27 April 2026", text)
        self.assertIn("Subject: Application for Medical Representative Position", text)
        self.assertIn("Dear Hiring Manager,", text)
        self.assertIn("Yours faithfully,", text)
        self.assertTrue(text.endswith("Test User"))


if __name__ == "__main__":
    unittest.main()
