from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from core.models import Study, Attribute

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
	"""Validate transform code with a conservative AST whitelist."""
	import ast

	if not code:
		return

	try:
		tree = ast.parse(code, mode="exec")
	except SyntaxError as e:
		raise ValidationError(f"Transform code has a syntax error: {e}")

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
	)
	banned_names = {"__import__", "open", "exec", "eval", "compile", "globals", "locals", "input", "help"}

	class SafeVisitor(ast.NodeVisitor):
		def visit(self, node):
			if not isinstance(node, allowed_nodes):
				raise ValidationError(f"Unsupported Python construct in transform code: {type(node).__name__}")
			return super().visit(node)

		def visit_Call(self, node: ast.Call):
			safe_call_names = {"int", "float", "str", "bool", "round", "abs", "min", "max", "len"}
			if isinstance(node.func, ast.Name):
				if node.func.id in banned_names or node.func.id not in safe_call_names:
					raise ValidationError(f"Call to disallowed function: {getattr(node.func, 'id', '')}")
			elif isinstance(node.func, ast.Attribute):
				raise ValidationError("Attribute function calls are not allowed in transform code.")
			self.generic_visit(node)

		def visit_Attribute(self, node: ast.Attribute):
			if isinstance(node.value, ast.Name) and node.value.id == "value":
				return
			raise ValidationError("Attribute access is restricted in transform code.")

		def visit_Name(self, node: ast.Name):
			if node.id in banned_names:
				raise ValidationError(f"Use of banned identifier: {node.id}")

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

