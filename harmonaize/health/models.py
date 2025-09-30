from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from core.models import Study, Attribute
import os

User = get_user_model()


class MappingSchema(models.Model):
	"""Defines a mapping configuration from a source study to a target study.

	Patient/datetime/related-person handled per MappingRule now.
	"""

	STATUS_CHOICES = (
		("provisional", "Provisional"),
		("approved", "Approved"),
	)

	RELATION_CHOICES = (
		("self", "Self"),
		("child", "Child"),
		("father", "Father"),
		("mother", "Mother"),
		("spouse", "Spouse/Partner"),
		("sibling", "Sibling"),
		("other", "Other"),
	)

	# Universal settings for auto-population
	universal_patient_id = models.ForeignKey(
		Attribute,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='as_universal_patient_id',
		help_text="Default source attribute to use as patient ID for all mappings",
	)
	universal_datetime = models.ForeignKey(
		Attribute,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name='as_universal_datetime',
		help_text="Default source attribute to use as datetime for all mappings",
	)
	universal_relation_type = models.CharField(
		max_length=20,
		choices=RELATION_CHOICES,
		default="self",
		help_text="Default relationship type for related patient mappings",
	)
	auto_populate_enabled = models.BooleanField(
		default=False,
		help_text="Whether to auto-populate mapping rules based on universal settings",
	)

	source_study = models.ForeignKey(
		Study,
		on_delete=models.CASCADE,
		related_name="source_mappings",
		help_text="Study containing source attributes to harmonise",
	)
	target_study = models.ForeignKey(
		Study,
		on_delete=models.CASCADE,
		related_name="target_mappings",
		help_text="Target database (study) defining harmonised attributes",
	)

	# Approval metadata
	status = models.CharField(
		max_length=20,
		choices=STATUS_CHOICES,
		default="provisional",
	)
	approved_by = models.ForeignKey(
		User,
		on_delete=models.SET_NULL,
		null=True,
		blank=True,
		related_name="approved_mappings",
	)
	approved_at = models.DateTimeField(null=True, blank=True)

	comments = models.TextField(blank=True)

	created_by = models.ForeignKey(
		User,
		on_delete=models.CASCADE,
		related_name="created_mappings",
	)
	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		ordering = ["-created_at"]
		verbose_name = "Mapping Schema"
		verbose_name_plural = "Mapping Schemas"

	def __str__(self) -> str:
		return f"Mapping {self.pk} ({self.source_study} → {self.target_study})"

	def clean(self):
		if self.source_study_id and self.source_study.study_purpose != "source":
			raise ValidationError(
				{"source_study": "Source study must have purpose 'source'."}
			)
		if self.target_study_id and self.target_study.study_purpose != "target":
			raise ValidationError(
				{"target_study": "Target study must have purpose 'target'."}
			)


def validate_safe_transform_code(code: str):
    """Validate transform code with a conservative AST whitelist allowing safe method calls."""
    import ast

    if not code:
        return

    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError as e:
        msg = f"Transform code has a syntax error: {e}"
        raise ValidationError(msg) from e

    allowed_nodes = (
        ast.Module, ast.Expr, ast.Assign, ast.Return,
        ast.Lambda, ast.FunctionDef, ast.arguments, ast.arg,
        ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
        ast.Call, ast.Name, ast.Load, ast.Store,
        ast.Num, ast.Str, ast.Constant, ast.List, ast.Tuple, ast.Dict,
        ast.Attribute, ast.Subscript, ast.Slice,
        ast.NameConstant,
        ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod,
        ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd,
        ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
        ast.In, ast.NotIn, ast.Is, ast.IsNot,  # Add missing comparison operators
        # Allow basic control flow and comprehensions
        ast.If, ast.ListComp, ast.DictComp, ast.SetComp, ast.GeneratorExp,
        ast.comprehension,
    )
    banned_names = {"__import__", "open", "exec", "eval", "compile", "globals", "locals", "input", "help"}
    
    # Safe method names that can be called on objects
    safe_string_methods = {
        "upper", "lower", "title", "capitalize", "strip", "lstrip", "rstrip",
        "split", "rsplit", "join", "replace", "find", "rfind", "index", "rindex",
        "count", "startswith", "endswith", "isdigit", "isalpha", "isalnum", 
        "isspace", "islower", "isupper", "istitle", "zfill", "ljust", "rjust", 
        "center", "partition", "rpartition", "swapcase", "translate", "encode",
    }
    
    safe_list_methods = {
        "append", "extend", "insert", "remove", "pop", "clear", "index", "count",
        "sort", "reverse", "copy",
    }
    
    safe_dict_methods = {
        "keys", "values", "items", "get", "pop", "clear", "copy", "update",
    }
    
    # All safe methods combined
    safe_methods = safe_string_methods | safe_list_methods | safe_dict_methods

    class SafeVisitor(ast.NodeVisitor):
        def visit(self, node):
            if not isinstance(node, allowed_nodes):
                msg = f"Unsupported Python construct in transform code: {type(node).__name__}"
                raise ValidationError(msg)
            return super().visit(node)

        def visit_Call(self, node: ast.Call):
            safe_call_names = {"int", "float", "str", "bool", "round", "abs", "min", "max", "len", "sum", "any", "all", "sorted", "reversed"}
            
            if isinstance(node.func, ast.Name):
                # Direct function calls like int(), str(), etc.
                if node.func.id in banned_names or node.func.id not in safe_call_names:
                    msg = f"Call to disallowed function: {getattr(node.func, 'id', '')}"
                    raise ValidationError(msg)
            elif isinstance(node.func, ast.Attribute):
                # Method calls like value.upper(), mylist.append(), etc.
                method_name = node.func.attr
                if method_name not in safe_methods:
                    msg = f"Method call not allowed: {method_name}"
                    raise ValidationError(msg)
            else:
                msg = "Complex function calls are not allowed in transform code."
                raise ValidationError(msg)
            
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute):
            # Block access to dangerous attributes like __class__, __dict__, etc.
            if node.attr.startswith('__') and node.attr.endswith('__'):
                msg = f"Access to dunder attribute not allowed: {node.attr}"
                raise ValidationError(msg)
            
            # Allow attribute access for safe method calls and the 'value' variable
            if isinstance(node.value, ast.Name):
                # Allow access to 'value' variable and its attributes
                if node.value.id == "value":
                    return
                # Allow access to other variables for method calls
                return
            if isinstance(node.value, ast.Attribute):
                # Allow chained attribute access (method calls validated separately)
                return
            if isinstance(node.value, ast.Call):
                # Allow attribute access on results of function calls
                return
            
            # Allow the attribute access - method call validation happens in visit_Call
            return

        def visit_Name(self, node: ast.Name):
            if node.id in banned_names:
                msg = f"Use of banned identifier: {node.id}"
                raise ValidationError(msg)

    SafeVisitor().visit(tree)


class MappingRule(models.Model):
	"""A single mapping from one source attribute to one target attribute."""

	ROLE_CHOICES = (
		("value", "Value"),
		("patient_id", "Patient ID"),
		("datetime", "Date/Time"),
		("related_patient_id", "Related Patient ID"),
	)
	schema = models.ForeignKey(MappingSchema, on_delete=models.CASCADE, related_name="rules")
	source_attribute = models.ForeignKey(
		Attribute, on_delete=models.CASCADE, related_name="as_source_in_rules",
		help_text="Attribute from the source study",
	)
	# Flag to mark variables as not mappable
	not_mappable = models.BooleanField(
		default=False,
		help_text="Mark this variable as not mappable to any target variable"
	)
	target_attribute = models.ForeignKey(
		Attribute, on_delete=models.CASCADE, related_name="as_target_in_rules",
		help_text="Attribute from the target study",
		null=True, blank=True,
	)
	role = models.CharField(max_length=32, choices=ROLE_CHOICES, default="value")
	
	# Individual patient_id and datetime for this mapping rule
	patient_id_attribute = models.ForeignKey(
		Attribute, on_delete=models.SET_NULL, null=True, blank=True,
		related_name="as_patient_id_for_rules",
		help_text="Patient ID attribute to use for this mapping",
	)
	datetime_attribute = models.ForeignKey(
		Attribute, on_delete=models.SET_NULL, null=True, blank=True,
		related_name="as_datetime_for_rules", 
		help_text="DateTime attribute to use for this mapping",
	)
	
	related_relation_type = models.CharField(
		max_length=20,
		choices=MappingSchema.RELATION_CHOICES,
		blank=True,
		help_text="Relation type (only for related patient id role)",
	)
	transform_code = models.TextField(blank=True, help_text="Optional safe Python: lambda value: ... or def transform(value): return ...")
	comments = models.TextField(blank=True)

	created_at = models.DateTimeField(auto_now_add=True)
	updated_at = models.DateTimeField(auto_now=True)

	class Meta:
		unique_together = ("schema", "source_attribute")
		ordering = ["source_attribute__variable_name"]

	def __str__(self) -> str:
		return f"{self.source_attribute} → {self.target_attribute}"

	def clean(self):
		errors = {}
		if self.schema_id and self.source_attribute_id:
			if not self.schema.source_study.variables.filter(pk=self.source_attribute_id).exists():
				errors["source_attribute"] = "Must be an attribute of the source study."
		if self.schema_id and self.target_attribute_id:
			if not self.schema.target_study.variables.filter(pk=self.target_attribute_id).exists():
				errors["target_attribute"] = "Must be an attribute of the target study."

		try:
			validate_safe_transform_code(self.transform_code or "")
		except ValidationError as e:
			errors["transform_code"] = e.messages

		if errors:
			raise ValidationError(errors)


class RawDataFile(models.Model):
    """
    Stores uploaded raw data files with metadata for processing.
    Links to Study and tracks patient ID and date columns.
    """
    # Basic file information
    study = models.ForeignKey(
        Study,
        on_delete=models.CASCADE,
        related_name="raw_data_files",
        help_text="Study this raw data belongs to"
    )
    file = models.FileField(
        upload_to='raw_data/%Y/%m/%d/',
        help_text="Raw data file (CSV, Excel, etc.)"
    )
    original_filename = models.CharField(
        max_length=255,
        help_text="Original filename when uploaded"
    )
    
    # File metadata
    file_format = models.CharField(
        max_length=20,
        choices=[
            ('csv', 'CSV'),
            ('xlsx', 'Excel (XLSX)'),
            ('xls', 'Excel (XLS)'),
            ('json', 'JSON'),
            ('txt', 'Text'),
        ],
        help_text="Detected or specified file format"
    )
    file_size = models.PositiveIntegerField(
        help_text="File size in bytes"
    )
    rows_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of data rows in the file"
    )
    columns_count = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Number of columns in the file"
    )
    
    # Key column identification for data linkage
    patient_id_column = models.CharField(
        max_length=200,
        blank=True,
        help_text="Column name containing patient identifiers"
    )
    date_column = models.CharField(
        max_length=200,
        blank=True,
        help_text="Column name containing dates/timestamps"
    )
    
    # Processing status
    PROCESSING_STATUS_CHOICES = [
        ("uploaded", "Uploaded"),
        ("validation_error", "Validation Error"),
        ("validated", "Validated"),
        ("processed", "Processed"),
        ("processing", "Processing"),
        ("ingestion_error", "Ingestion Error"),
        ("ingested", "Ingested"),
        ("processed_with_errors", "Processed with Errors"),
        ("error", "Processing Error"),  # Generic fallback
    ]
    processing_status = models.CharField(
        max_length=30,
        choices=PROCESSING_STATUS_CHOICES,
        default="uploaded",
    )
    processing_message = models.TextField(
        blank=True,
        help_text="Status messages or error details"
    )
    
    # Timestamps and user tracking
    uploaded_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="uploaded_raw_data"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the file was last processed"
    )

    # Content fingerprint for duplicate detection
    checksum = models.CharField(
        max_length=64,
        blank=True,
        help_text="SHA256 checksum of the uploaded file content",
    )

    # Column comparison snapshot at upload time
    detected_columns = models.JSONField(
        default=list,
        blank=True,
        help_text="Detected column names from the file header",
    )
    expected_attributes = models.JSONField(
        default=list,
        blank=True,
        help_text="Expected attribute names from the study's codebook at upload time",
    )
    extra_columns = models.JSONField(
        default=list,
        blank=True,
        help_text="Columns present in the file but not found in the study's attributes",
    )
    missing_attributes = models.JSONField(
        default=list,
        blank=True,
        help_text="Attributes expected by the study but missing from the file",
    )
    has_attribute_mismatches = models.BooleanField(
        default=False,
        help_text="Whether detected columns differ from expected attributes",
    )

    # Harmonisation transformation tracking (set when started from harmonisation dashboard)
    TRANSFORMATION_STATUS_CHOICES = [
        ("not_started", "Not Started"),
        ("in_progress", "Transformation In Progress"),
        ("completed", "Transformed"),
        ("failed", "Transformation Failed"),
    ]
    transformation_status = models.CharField(
        max_length=20,
        choices=TRANSFORMATION_STATUS_CHOICES,
        default="not_started",
        help_text="Status of harmonisation transformation for this file",
    )
    transformation_message = models.TextField(
        blank=True,
        help_text="Notes or errors from the last harmonisation transformation run",
    )
    last_transformation_schema = models.ForeignKey(
        'MappingSchema',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transformed_files',
        help_text="The mapping schema used in the last harmonisation transformation",
    )
    transformation_started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When harmonisation transformation started for this file",
    )
    transformed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When harmonisation transformation completed for this file",
    )
    
    class Meta:
        ordering = ['-uploaded_at']
        indexes = [
            models.Index(fields=['study', 'checksum'], name='rdfile_study_checksum_idx'),
        ]
        verbose_name = "Raw Data File"
        verbose_name_plural = "Raw Data Files"
    
    def __str__(self):
        return f"{self.original_filename} ({self.study.name})"
    
    def save(self, *args, **kwargs):
        # Auto-detect file format if not set
        if not self.file_format and self.file:
            ext = os.path.splitext(self.original_filename or self.file.name)[1].lower()
            format_map = {
                '.csv': 'csv',
                '.xlsx': 'xlsx',
                '.xls': 'xls',
                '.json': 'json',
                '.txt': 'txt',
            }
            self.file_format = format_map.get(ext, 'csv')
        
        # Set file size if not set
        if self.file and not self.file_size:
            self.file_size = self.file.size
        
        # Set original filename if not set
        if not self.original_filename and self.file:
            self.original_filename = self.file.name
            
        super().save(*args, **kwargs)


class RawDataColumn(models.Model):
    """
    Metadata about columns in raw data files.
    Discovered during file validation/processing.
    """
    raw_data_file = models.ForeignKey(
        RawDataFile,
        on_delete=models.CASCADE,
        related_name="columns"
    )
    column_name = models.CharField(
        max_length=200,
        help_text="Original column name from the file"
    )
    column_index = models.PositiveIntegerField(
        help_text="0-based column index in the file"
    )
    
    # Inferred column metadata
    sample_values = models.JSONField(
        default=list,
        blank=True,
        help_text="Sample values from this column for preview"
    )
    inferred_type = models.CharField(
        max_length=50,
        choices=[
            ('text', 'Text'),
            ('integer', 'Integer'),
            ('float', 'Float'),
            ('date', 'Date'),
            ('datetime', 'Date/Time'),
            ('boolean', 'Boolean'),
            ('categorical', 'Categorical'),
        ],
        default='text'
    )
    non_null_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of non-null values in this column"
    )
    unique_count = models.PositiveIntegerField(
        default=0,
        help_text="Number of unique values in this column"
    )
    
    # Potential mapping hints
    is_potential_patient_id = models.BooleanField(
        default=False,
        help_text="Could this be a patient ID column?"
    )
    is_potential_date = models.BooleanField(
        default=False,
        help_text="Could this be a date/time column?"
    )
    
    # Variable mapping
    mapped_variable = models.ForeignKey(
        "core.Attribute",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text="Study variable this column maps to"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['column_index']
        unique_together = ['raw_data_file', 'column_name']
        verbose_name = "Raw Data Column"
        verbose_name_plural = "Raw Data Columns"
    
    def __str__(self):
        return f"{self.column_name} ({self.raw_data_file.original_filename})"

