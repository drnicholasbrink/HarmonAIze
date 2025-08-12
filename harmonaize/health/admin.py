from django.contrib import admin
from .models import MappingSchema, MappingRule


@admin.register(MappingSchema)
class MappingSchemaAdmin(admin.ModelAdmin):
	list_display = (
		"source_study",
		"target_study",
		"status",
		"approved_by",
		"approved_at",
		"created_by",
		"created_at",
	)
	list_filter = ("status", "created_at")
	search_fields = ("source_study__name", "target_study__name")


@admin.register(MappingRule)
class MappingRuleAdmin(admin.ModelAdmin):
	list_display = (
		"schema",
		"source_attribute",
		"target_attribute",
		"role",
		"updated_at",
	)
	search_fields = (
		"schema__source_study__name",
		"schema__target_study__name",
		"source_attribute__variable_name",
		"target_attribute__variable_name",
	)
	list_filter = ("schema", "role")

