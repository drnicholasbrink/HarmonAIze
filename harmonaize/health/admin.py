from django.contrib import admin
from .models import (
	MappingSchema,
	MappingRule,
	RawDataFile,
	RawDataColumn,
)


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


class RawDataColumnInline(admin.TabularInline):
	model = RawDataColumn
	extra = 0
	fields = (
		"column_index",
		"column_name",
		"mapped_variable",
		"inferred_type",
		"non_null_count",
		"unique_count",
		"is_potential_patient_id",
		"is_potential_date",
	)
	readonly_fields = (
		"column_index",
		"column_name",
		"inferred_type",
		"non_null_count",
		"unique_count",
		"is_potential_patient_id",
		"is_potential_date",
	)


@admin.register(RawDataFile)
class RawDataFileAdmin(admin.ModelAdmin):
	list_display = (
		"original_filename",
		"study",
		"file_format",
		"file_size",
		"rows_count",
		"columns_count",
		"processing_status",
		"uploaded_by",
		"uploaded_at",
		"processed_at",
		"transformation_status",
		"transformed_at",
	)
	list_filter = (
		"processing_status",
		"transformation_status",
		"file_format",
		"study",
		"uploaded_at",
		"processed_at",
		"transformed_at",
	)
	search_fields = (
		"original_filename",
		"study__name",
		"uploaded_by__username",
		"uploaded_by__email",
	)
	readonly_fields = (
		"checksum",
		"file_size",
		"rows_count",
		"columns_count",
		"uploaded_at",
		"updated_at",
		"processed_at",
	)
	inlines = [RawDataColumnInline]


@admin.register(RawDataColumn)
class RawDataColumnAdmin(admin.ModelAdmin):
	list_display = (
		"column_index",
		"column_name",
		"raw_data_file",
		"mapped_variable",
		"inferred_type",
		"non_null_count",
		"unique_count",
		"is_potential_patient_id",
		"is_potential_date",
		"created_at",
	)
	list_filter = (
		"inferred_type",
		"is_potential_patient_id",
		"is_potential_date",
		"raw_data_file__study",
	)
	search_fields = (
		"column_name",
		"raw_data_file__original_filename",
		"mapped_variable__variable_name",
	)
	raw_id_fields = ("raw_data_file",)

