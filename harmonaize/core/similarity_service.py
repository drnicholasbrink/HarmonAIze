"""
Semantic similarity service for variable mapping using cosine similarity.
"""
import logging
import numpy as np

from .models import Attribute
from .embedding_service import embedding_service

logger = logging.getLogger(__name__)


class SimilarityService:
    """Service for computing semantic similarity between attributes."""

    # Similarity score thresholds
    EXCELLENT_THRESHOLD = 0.85
    GOOD_THRESHOLD = 0.70
    FAIR_THRESHOLD = 0.55
    POOR_THRESHOLD = 0.40

    def __init__(self):
        self.embedding_service = embedding_service
        self.description_weight = 0.7
        self.name_weight = 0.3

    def compute_similarity_score(
        self,
        embedding1: np.ndarray,
        embedding2: np.ndarray,
    ) -> float:
        """
        Compute cosine similarity between two embeddings.

        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector

        Returns:
            Cosine similarity score (0-1)
        """
        # Normalize embeddings
        norm1 = np.linalg.norm(embedding1)
        norm2 = np.linalg.norm(embedding2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        # Compute cosine similarity
        similarity = np.dot(embedding1, embedding2) / (norm1 * norm2)

        # Ensure result is in [0, 1] range
        return max(0.0, min(1.0, similarity))

    def find_similar_attributes(
        self,
        source_attribute: Attribute,
        target_attributes: list[Attribute],
        limit: int = 10,
    ) -> list[dict]:
        """
        Find similar target attributes using name and description similarity.

        Args:
            source_attribute: Source attribute to find matches for
            target_attributes: List of target attributes to search in
            limit: Maximum number of results to return

        Returns:
            List of similarity results
        """
        # Check if source has required embeddings
        if source_attribute.name_embedding is None:
            logger.warning(
                "Source attribute %s missing name embedding",
                source_attribute.id,
            )
            return []

        source_name_emb = np.array(
            source_attribute.name_embedding,
            dtype=np.float32,
        )
        source_desc_emb = None
        if source_attribute.description_embedding is not None:
            source_desc_emb = np.array(
                source_attribute.description_embedding,
                dtype=np.float32,
            )

        # Filter target attributes that have name embeddings
        valid_targets = [
            attr for attr in target_attributes
            if attr.name_embedding is not None
        ]

        if not valid_targets:
            logger.warning("No target attributes with embeddings found")
            return []

        # Compute similarities for each target
        similarities = []
        for target_attr in valid_targets:
            target_name_emb = np.array(
                target_attr.name_embedding,
                dtype=np.float32,
            )

            # Compute name similarity
            name_similarity = self.compute_similarity_score(
                source_name_emb,
                target_name_emb,
            )

            # Compute description similarity if both have descriptions
            description_similarity = None
            if (source_desc_emb is not None and
                target_attr.description_embedding is not None):
                target_desc_emb = np.array(
                    target_attr.description_embedding,
                    dtype=np.float32,
                )
                description_similarity = self.compute_similarity_score(
                    source_desc_emb,
                    target_desc_emb,
                )

            # Compute combined weighted score
            if description_similarity is not None:
                combined_similarity = (
                    self.description_weight * description_similarity +
                    self.name_weight * name_similarity
                )
            else:
                # Use only name similarity if description not available
                combined_similarity = name_similarity

            confidence_grade = self._grade_similarity_confidence(
                combined_similarity,
            )

            similarities.append({
                "attribute_id": target_attr.id,
                "variable_name": target_attr.variable_name,
                "display_name": (
                    target_attr.display_name or
                    target_attr.variable_name
                ),
                "description": target_attr.description or "",
                "variable_type": target_attr.variable_type,
                "unit": target_attr.unit or "",
                "name_similarity": float(name_similarity),
                "description_similarity": (
                    float(description_similarity)
                    if description_similarity is not None
                    else None
                ),
                "combined_similarity": float(combined_similarity),
                "confidence_grade": confidence_grade,
                "confidence_label": self._get_confidence_label(
                    confidence_grade,
                ),
                "confidence_color": self._get_confidence_color(
                    confidence_grade,
                ),
                "has_description_match": description_similarity is not None,
            })

        # Sort by combined similarity and return top results
        similarities.sort(
            key=lambda x: x["combined_similarity"],
            reverse=True,
        )
        return similarities[:limit]

    def _grade_similarity_confidence(self, similarity_score: float) -> str:
        """
        Grade the similarity confidence based on the score.

        Args:
            similarity_score: Cosine similarity score (0-1)

        Returns:
            Confidence grade string
        """
        if similarity_score >= self.EXCELLENT_THRESHOLD:
            return "excellent"
        if similarity_score >= self.GOOD_THRESHOLD:
            return "good"
        if similarity_score >= self.FAIR_THRESHOLD:
            return "fair"
        if similarity_score >= self.POOR_THRESHOLD:
            return "poor"
        return "very_poor"

    def _get_confidence_label(self, grade: str) -> str:
        """Get human-readable label for confidence grade."""
        labels = {
            "excellent": "Excellent Match",
            "good": "Good Match",
            "fair": "Fair Match",
            "poor": "Poor Match",
            "very_poor": "Very Poor Match",
        }
        return labels.get(grade, "Unknown")

    def _get_confidence_color(self, grade: str) -> str:
        """Get color class for confidence grade."""
        colors = {
            "excellent": "success",  # Green
            "good": "info",         # Blue
            "fair": "warning",      # Yellow/Orange
            "poor": "danger",       # Red
            "very_poor": "secondary", # Gray
        }
        return colors.get(grade, "secondary")

    def batch_find_similar_attributes(
        self,
        source_attributes: list[Attribute],
        target_attributes: list[Attribute],
        limit_per_source: int = 5,
    ) -> dict[int, list[dict]]:
        """
        Find similar attributes for multiple source attributes in batch.

        Args:
            source_attributes: List of source attributes
            target_attributes: List of target attributes to search in
            limit_per_source: Maximum number of results per source attribute

        Returns:
            Dictionary mapping source attribute IDs to their results
        """
        results = {}

        for source_attr in source_attributes:
            try:
                similarities = self.find_similar_attributes(
                    source_attr,
                    target_attributes,
                    limit=limit_per_source,
                )
                results[source_attr.id] = similarities
            except Exception:
                logger.exception(
                    "Error finding similarities for attribute %s",
                    source_attr.id,
                )
                results[source_attr.id] = []

        return results

    def get_mapping_suggestions(
        self,
        source_study_id: int,
        target_study_id: int,
        limit_per_source: int = 5,
    ) -> dict[int, list[dict]]:
        """
        Get mapping suggestions between source and target studies.

        Args:
            source_study_id: ID of the source study
            target_study_id: ID of the target study
            limit_per_source: Maximum suggestions per source variable

        Returns:
            Dictionary mapping source attribute IDs to their suggestions
        """
        try:
            # Get source attributes (only those with embeddings)
            source_attributes = list(Attribute.objects.filter(
                studies__id=source_study_id,
                source_type="source",
                name_embedding__isnull=False,
            ).distinct())

            # Get target attributes (only those with embeddings)
            target_attributes = list(Attribute.objects.filter(
                studies__id=target_study_id,
                source_type="target",
                name_embedding__isnull=False,
            ).distinct())

            if not source_attributes:
                logger.warning(
                    "No source attributes with embeddings found for study %s",
                    source_study_id,
                )
                return {}

            if not target_attributes:
                logger.warning(
                    "No target attributes with embeddings found for study %s",
                    target_study_id,
                )
                return {}

            # Get batch similarities
            return self.batch_find_similar_attributes(
                source_attributes,
                target_attributes,
                limit_per_source,
            )

        except Exception:
            logger.exception("Error getting mapping suggestions")
            return {}


# Global instance
similarity_service = SimilarityService()