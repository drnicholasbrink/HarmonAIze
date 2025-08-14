from django.test import TestCase
from django.core.exceptions import ValidationError
from .models import validate_safe_transform_code


class TransformCodeValidationTests(TestCase):
    """Test the safe transform code validation function."""

    def test_safe_string_methods_allowed(self):
        """Test that safe string methods are allowed."""
        safe_codes = [
            "lambda value: value.upper()",
            "lambda value: value.lower().strip()",
            "lambda value: value.split(',')",
            "lambda value: value.replace('old', 'new')",
            "lambda value: ' '.join(value.split())",
            "lambda value: value.startswith('prefix')",
            "lambda value: value.endswith('suffix')",
            "lambda value: value.find('substring')",
            "lambda value: value.count('char')",
            "lambda value: value.capitalize().title()",
        ]
        
        for code in safe_codes:
            with self.subTest(code=code):
                try:
                    validate_safe_transform_code(code)
                except ValidationError:
                    self.fail(f"Safe code should not raise ValidationError: {code}")

    def test_safe_built_in_functions_allowed(self):
        """Test that safe built-in functions are allowed."""
        safe_codes = [
            "lambda value: int(value) if value else 0",
            "lambda value: float(value) * 2.54",
            "lambda value: str(value).upper()",
            "lambda value: len(value) if value else 0",
            "lambda value: min(1, 2, 3)",
            "lambda value: max([1, 2, 3])",
            "lambda value: abs(-5)",
            "lambda value: round(3.14159, 2)",
            "lambda value: sum([1, 2, 3])",
            "lambda value: sorted(value.split(','))",
        ]
        
        for code in safe_codes:
            with self.subTest(code=code):
                try:
                    validate_safe_transform_code(code)
                except ValidationError:
                    self.fail(f"Safe code should not raise ValidationError: {code}")

    def test_complex_safe_transforms(self):
        """Test more complex but safe transform combinations."""
        safe_codes = [
            "lambda value: value.strip().upper().replace(' ', '_') if value else None",
            "lambda value: float(value.replace(',', '.')) if value and value.replace(',', '.').replace('.', '').isdigit() else None",
            "lambda value: [x.strip() for x in value.split(',') if x.strip()] if value else []",
            """def transform(value):
    if not value:
        return None
    parts = value.strip().split()
    return ' '.join(part.capitalize() for part in parts)""",
        ]
        
        for code in safe_codes:
            with self.subTest(code=code):
                try:
                    validate_safe_transform_code(code)
                except ValidationError:
                    self.fail(f"Safe code should not raise ValidationError: {code}")

    def test_unsafe_functions_blocked(self):
        """Test that unsafe functions are properly blocked."""
        unsafe_codes = [
            "lambda value: eval(value)",
            "lambda value: exec('print(value)')",
            "lambda value: open('file.txt').read()",
            "lambda value: __import__('os').system('ls')",
            "lambda value: compile('print(1)', 'string', 'exec')",
            "lambda value: globals()",
            "lambda value: locals()",
        ]
        
        for code in unsafe_codes:
            with self.subTest(code=code):
                with self.assertRaises(ValidationError):
                    validate_safe_transform_code(code)

    def test_unsafe_methods_blocked(self):
        """Test that unsafe or non-whitelisted methods are blocked."""
        unsafe_codes = [
            "lambda value: value.__class__",
            "lambda value: value.__dict__",
            "lambda value: value.dangerous_method()",
            "lambda value: value.system('rm -rf /')",
            "lambda value: getattr(value, '__class__')",
        ]
        
        for code in unsafe_codes:
            with self.subTest(code=code):
                with self.assertRaises(ValidationError):
                    validate_safe_transform_code(code)

    def test_syntax_errors_caught(self):
        """Test that syntax errors are properly caught."""
        invalid_codes = [
            "lambda value: value.upper(",  # Missing closing parenthesis
            "lambda value value.upper()",  # Missing colon
            "def transform(value)\n    return value",  # Missing colon
            "lambda value: if value else None",  # Invalid syntax
        ]
        
        for code in invalid_codes:
            with self.subTest(code=code):
                with self.assertRaises(ValidationError):
                    validate_safe_transform_code(code)

    def test_empty_code_allowed(self):
        """Test that empty or None code is allowed."""
        safe_codes = [None, "", "   ", "\n\n"]
        
        for code in safe_codes:
            with self.subTest(code=code):
                try:
                    validate_safe_transform_code(code)
                except ValidationError:
                    self.fail(f"Empty code should not raise ValidationError: {repr(code)}")

    def test_list_and_dict_methods_allowed(self):
        """Test that safe list and dict methods are allowed."""
        safe_codes = [
            "lambda value: value.split(',').append('new')",
            "lambda value: value.split().sort()",
            "lambda value: {'a': 1}.keys()",
            "lambda value: {'a': 1}.get('a', 0)",
            "lambda value: [1, 2, 3].count(1)",
            "lambda value: [1, 2, 3].index(2)",
        ]
        
        for code in safe_codes:
            with self.subTest(code=code):
                try:
                    validate_safe_transform_code(code)
                except ValidationError:
                    self.fail(f"Safe code should not raise ValidationError: {code}")
