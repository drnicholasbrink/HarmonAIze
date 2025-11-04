"""
Celery tasks for generating embeddings asynchronously.
"""
import logging
from celery import shared_task
from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_attribute_embeddings(self, attribute_id: int):
    """
    Generate embeddings for an attribute's name and description.
    
    Args:
        attribute_id: The ID of the Attribute to generate embeddings for
        
    Returns:
        dict: Status information about the embedding generation
    """
    try:
        # Import here to avoid circular imports
        from core.models import Attribute
        from core.embedding_service import embedding_service
        
        logger.info(f"Starting embedding generation for Attribute {attribute_id}")
        
        # Get the attribute
        try:
            attribute = Attribute.objects.get(id=attribute_id)
        except ObjectDoesNotExist:
            logger.error(f"Attribute with ID {attribute_id} does not exist")
            return {
                "success": False,
                "error": f"Attribute with ID {attribute_id} not found",
                "attribute_id": attribute_id
            }
        
        # Generate embeddings
        name_embedding, description_embedding = embedding_service.generate_attribute_embeddings(
            variable_name=attribute.variable_name,
            description=attribute.description
        )
        
        # Validate embeddings
        name_valid = embedding_service.validate_embedding_dimensions(name_embedding)
        description_valid = (
            embedding_service.validate_embedding_dimensions(description_embedding)
            if description_embedding is not None else True  # None is valid for empty descriptions
        )
        
        if not name_valid:
            error_msg = f"Invalid name embedding generated for Attribute {attribute_id}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "attribute_id": attribute_id
            }
        
        # Save embeddings to database
        with transaction.atomic():
            attribute.name_embedding = name_embedding.tolist() if name_embedding is not None else None
            attribute.description_embedding = description_embedding.tolist() if description_embedding is not None else None
            attribute.save(update_fields=['name_embedding', 'description_embedding'])
        
        logger.info(f"Successfully generated embeddings for Attribute {attribute_id}")
        
        return {
            "success": True,
            "attribute_id": attribute_id,
            "name_embedding_generated": name_embedding is not None,
            "description_embedding_generated": description_embedding is not None,
            "variable_name": attribute.variable_name
        }
        
    except Exception as exc:
        # Log the error
        logger.error(f"Error generating embeddings for Attribute {attribute_id}: {exc}")
        
        # Retry the task with exponential backoff
        try:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        except self.MaxRetriesExceededError:
            logger.error(f"Max retries exceeded for Attribute {attribute_id} embedding generation")
            return {
                "success": False,
                "error": f"Max retries exceeded: {str(exc)}",
                "attribute_id": attribute_id
            }


@shared_task
def generate_embeddings_for_study(study_id: int):
    """
    Generate embeddings for all attributes in a study.
    
    Args:
        study_id: The ID of the Study to generate embeddings for
        
    Returns:
        dict: Summary of embedding generation for the study
    """
    try:
        # Import here to avoid circular imports
        from core.models import Study
        
        logger.info(f"Starting batch embedding generation for Study {study_id}")
        
        # Get the study
        try:
            study = Study.objects.get(id=study_id)
        except ObjectDoesNotExist:
            logger.error(f"Study with ID {study_id} does not exist")
            return {
                "success": False,
                "error": f"Study with ID {study_id} not found",
                "study_id": study_id
            }
        
        # Get all attributes for this study
        attributes = study.variables.all()
        total_attributes = attributes.count()
        
        if total_attributes == 0:
            return {
                "success": True,
                "study_id": study_id,
                "study_name": study.name,
                "total_attributes": 0,
                "message": "No attributes found for this study"
            }
        
        # Queue embedding generation tasks for each attribute
        task_ids = []
        for attribute in attributes:
            task = generate_attribute_embeddings.delay(attribute.id)
            task_ids.append(task.id)
        
        logger.info(f"Queued {len(task_ids)} embedding tasks for Study {study_id}")
        
        return {
            "success": True,
            "study_id": study_id,
            "study_name": study.name,
            "total_attributes": total_attributes,
            "queued_tasks": len(task_ids),
            "task_ids": task_ids
        }
        
    except Exception as exc:
        logger.error(f"Error generating embeddings for Study {study_id}: {exc}")
        return {
            "success": False,
            "error": str(exc),
            "study_id": study_id
        }


@shared_task
def regenerate_attribute_embeddings(attribute_id: int):
    """
    Regenerate embeddings for an attribute (e.g., after name or description changes).
    
    Args:
        attribute_id: The ID of the Attribute to regenerate embeddings for
        
    Returns:
        dict: Status information about the embedding regeneration
    """
    # This is essentially the same as generate_attribute_embeddings but with logging
    # to distinguish between initial generation and regeneration
    logger.info(f"Regenerating embeddings for Attribute {attribute_id}")
    result = generate_attribute_embeddings(attribute_id)
    
    if result.get("success"):
        logger.info(f"Successfully regenerated embeddings for Attribute {attribute_id}")
    else:
        logger.error(f"Failed to regenerate embeddings for Attribute {attribute_id}: {result.get('error')}")
    
    return result


@shared_task
def check_missing_embeddings():
    """
    Check for attributes that are missing embeddings and queue generation tasks.
    
    Returns:
        dict: Summary of attributes found missing embeddings
    """
    try:
        # Import here to avoid circular imports
        from core.models import Attribute
        
        logger.info("Checking for attributes missing embeddings")
        
        # Find attributes missing name embeddings
        missing_name_embeddings = Attribute.objects.filter(name_embedding__isnull=True)
        
        # Find attributes missing description embeddings (but have descriptions)
        missing_description_embeddings = Attribute.objects.filter(
            description_embedding__isnull=True
        ).exclude(description__in=['', None])
        
        total_missing = missing_name_embeddings.count()
        description_missing = missing_description_embeddings.count()
        
        # Queue tasks for missing embeddings
        task_ids = []
        for attribute in missing_name_embeddings:
            task = generate_attribute_embeddings.delay(attribute.id)
            task_ids.append(task.id)
        
        logger.info(f"Queued {len(task_ids)} embedding generation tasks for missing embeddings")
        
        return {
            "success": True,
            "attributes_missing_name_embeddings": total_missing,
            "attributes_missing_description_embeddings": description_missing,
            "queued_tasks": len(task_ids),
            "task_ids": task_ids
        }
        
    except Exception as exc:
        logger.error(f"Error checking for missing embeddings: {exc}")
        return {
            "success": False,
            "error": str(exc)
        }


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def generate_tsne_projections_for_project(self, project_id: int, embedding_type: str = "both"):
    """
    Generate t-SNE projections for all attributes in a project.
    
    Args:
        project_id: The ID of the Project to generate t-SNE projections for
        embedding_type: Type of embedding to project ('name', 'description', or 'both')
        
    Returns:
        dict: Status information about the t-SNE generation
    """
    try:
        # Import here to avoid circular imports
        from core.models import Project
        from core.tsne_service import tsne_service
        
        logger.info("Starting t-SNE projection generation for Project %d", project_id)
        
        # Get the project
        try:
            project = Project.objects.get(id=project_id)
        except ObjectDoesNotExist:
            logger.error("Project with ID %d does not exist", project_id)
            return {
                "success": False,
                "error": f"Project with ID {project_id} not found",
                "project_id": project_id,
            }
        
        # Generate t-SNE projections
        stats = tsne_service.project_attributes_by_project(
            project=project,
            embedding_type=embedding_type,
        )
        
        logger.info("Successfully generated t-SNE projections for Project %d", project_id)
        
        return {
            "success": True,
            "project_id": project_id,
            "project_name": project.name,
            "embedding_type": embedding_type,
            "statistics": stats,
        }
        
    except Exception as exc:
        # Log the error
        logger.exception("Error generating t-SNE projections for Project %d", project_id)
        
        # Retry the task with exponential backoff
        try:
            raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
        except self.MaxRetriesExceededError:
            logger.error("Max retries exceeded for Project %d t-SNE generation", project_id)
            return {
                "success": False,
                "error": f"Max retries exceeded: {str(exc)}",
                "project_id": project_id,
            }


@shared_task
def check_tsne_projection_progress(project_id: int):
    """
    Check the progress of t-SNE projections for a project.
    
    Args:
        project_id: The ID of the Project to check progress for
        
    Returns:
        dict: Progress information about t-SNE projections
    """
    try:
        # Import here to avoid circular imports
        from core.models import Project, Attribute
        
        # Get the project
        try:
            project = Project.objects.get(id=project_id)
        except ObjectDoesNotExist:
            return {
                "success": False,
                "error": f"Project with ID {project_id} not found",
                "project_id": project_id,
            }
        
        # Get all attributes in the project
        attributes = Attribute.objects.filter(
            studies__project=project,
        ).distinct()
        
        total_attributes = attributes.count()
        
        # Count attributes with t-SNE projections
        name_projections = attributes.filter(
            name_tsne_x__isnull=False,
            name_tsne_y__isnull=False,
        ).count()
        
        description_projections = attributes.filter(
            description_tsne_x__isnull=False,
            description_tsne_y__isnull=False,
        ).count()
        
        # Calculate percentages
        name_percentage = (name_projections / total_attributes * 100) if total_attributes > 0 else 0
        description_percentage = (description_projections / total_attributes * 100) if total_attributes > 0 else 0
        
        return {
            "success": True,
            "project_id": project_id,
            "project_name": project.name,
            "total_attributes": total_attributes,
            "name_projections": name_projections,
            "description_projections": description_projections,
            "name_percentage": round(name_percentage, 1),
            "description_percentage": round(description_percentage, 1),
            "is_complete": name_projections == total_attributes and description_projections == total_attributes,
        }
        
    except Exception as exc:
        logger.exception("Error checking t-SNE projection progress for Project %d", project_id)
        return {
            "success": False,
            "error": str(exc),
            "project_id": project_id,
        }