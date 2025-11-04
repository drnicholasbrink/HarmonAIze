"""
t-SNE projection service for generating 2D visualizations of high-dimensional embeddings.
"""
import logging
import numpy as np
import pandas as pd
from typing import Union, Optional, Dict, List
from sklearn.manifold import TSNE
from sklearn.preprocessing import StandardScaler
from django.conf import settings
from .models import Attribute, Project

logger = logging.getLogger(__name__)

# Constants
MIN_SAMPLES_FOR_TSNE = 2
DEFAULT_PERPLEXITY = 30
MAX_FALLBACK_PERPLEXITY = 30


class TSNEProjectionService:
    """Service for generating t-SNE projections of attribute embeddings."""
    def __init__(self, 
                 perplexity: float = 30.0,
                 n_iter: int = 1000,
                 learning_rate: float = 100.0,
                 early_exaggeration: float = 12.0,
                 random_state: int = 42):
        """
        Initialize the t-SNE projection service.
        
        Args:
            perplexity: The perplexity parameter for t-SNE
            n_iter: Maximum number of iterations for optimization
            learning_rate: Learning rate for t-SNE
            early_exaggeration: Early exaggeration factor
            random_state: Random state for reproducibility
        """
        self.perplexity = perplexity
        self.n_iter = n_iter
        self.learning_rate = learning_rate
        self.early_exaggeration = early_exaggeration
        self.random_state = random_state
        self.scaler = StandardScaler()
    
    def compute_tsne_projection(self, 
                               embeddings: np.ndarray,
                               perplexity: Optional[float] = None,
                               n_iter: Optional[int] = None) -> np.ndarray:
        """
        Compute t-SNE projection for a set of embeddings.
        
        Args:
            embeddings: Array of shape (n_samples, n_features) containing embeddings
            perplexity: Override default perplexity parameter
            n_iter: Override default number of iterations
            
        Returns:
            Array of shape (n_samples, 2) containing 2D coordinates
        """
        if embeddings.shape[0] < MIN_SAMPLES_FOR_TSNE:
            logger.warning("Need at least 2 embeddings for t-SNE projection")
            return np.zeros((embeddings.shape[0], 2))
        
        # Adjust perplexity if we have too few samples
        effective_perplexity = perplexity or self.perplexity
        if embeddings.shape[0] <= effective_perplexity:
            effective_perplexity = max(1, min(embeddings.shape[0] - 1, MAX_FALLBACK_PERPLEXITY))
            logger.info("Adjusted perplexity to %d for %d samples", effective_perplexity, embeddings.shape[0])
        
        try:
            # Standardize embeddings
            embeddings_scaled = self.scaler.fit_transform(embeddings)
            
            # Create t-SNE instance
            tsne = TSNE(
                n_components=2,
                perplexity=effective_perplexity,
                n_iter=n_iter or self.n_iter,
                learning_rate=self.learning_rate,
                early_exaggeration=self.early_exaggeration,
                random_state=self.random_state,
                verbose=1,  # Show progress
                metric='cosine'  # Use cosine distance for embeddings
            )
            
            # Compute projection
            coordinates = tsne.fit_transform(embeddings_scaled)
            
            logger.info(f"Successfully computed t-SNE projection for {embeddings.shape[0]} embeddings")
            return coordinates
            
        except Exception as e:
            logger.error(f"Error computing t-SNE projection: {e}")
            # Return zero coordinates as fallback
            return np.zeros((embeddings.shape[0], 2))
    
    def project_attributes_by_project(self, 
                                    project: Project,
                                    embedding_type: str = 'both') -> Dict[str, int]:
        """
        Compute t-SNE projections for all attributes in a project.
        
        Args:
            project: Project instance
            embedding_type: Type of embedding to project ('name', 'description', or 'both')
            
        Returns:
            Dictionary with statistics about the projection process
        """
        stats = {
            'total_attributes': 0,
            'projected_name': 0,
            'projected_description': 0,
            'skipped': 0
        }
        
        # Get all attributes in the project
        attributes = Attribute.objects.filter(
            studies__project=project
        ).distinct()
        
        stats['total_attributes'] = attributes.count()
        
        if stats['total_attributes'] == 0:
            logger.warning(f"No attributes found for project {project.name}")
            return stats
        
        # Project name embeddings
        if embedding_type in ['name', 'both']:
            name_stats = self._project_embeddings(
                attributes=attributes,
                embedding_field='name_embedding',
                x_field='name_tsne_x',
                y_field='name_tsne_y',
                embedding_type='name'
            )
            stats['projected_name'] = name_stats['projected']
            stats['skipped'] += name_stats['skipped']
        
        # Project description embeddings
        if embedding_type in ['description', 'both']:
            desc_stats = self._project_embeddings(
                attributes=attributes,
                embedding_field='description_embedding',
                x_field='description_tsne_x',
                y_field='description_tsne_y',
                embedding_type='description'
            )
            stats['projected_description'] = desc_stats['projected']
            stats['skipped'] += desc_stats['skipped']
        
        logger.info(f"t-SNE projection completed for project {project.name}: {stats}")
        return stats
    
    def _project_embeddings(self,
                           attributes,
                           embedding_field: str,
                           x_field: str,
                           y_field: str,
                           embedding_type: str) -> Dict[str, int]:
        """
        Helper method to project a specific type of embedding.
        
        Args:
            attributes: QuerySet of attributes
            embedding_field: Name of the embedding field
            x_field: Name of the x coordinate field
            y_field: Name of the y coordinate field
            embedding_type: Type of embedding for logging
            
        Returns:
            Dictionary with projection statistics
        """
        stats = {'projected': 0, 'skipped': 0}
        
        # Filter attributes that have the specified embedding
        attributes_with_embeddings = [
            attr for attr in attributes 
            if getattr(attr, embedding_field) is not None
        ]
        
        if not attributes_with_embeddings:
            logger.warning(f"No attributes with {embedding_type} embeddings found")
            stats['skipped'] = len(attributes)
            return stats
        
        # Extract embeddings
        embeddings = []
        for attr in attributes_with_embeddings:
            embedding = getattr(attr, embedding_field)
            if embedding is not None:
                embeddings.append(np.array(embedding))
        
        if not embeddings:
            logger.warning(f"No valid {embedding_type} embeddings found")
            stats['skipped'] = len(attributes)
            return stats
        
        # Convert to numpy array
        embeddings_array = np.array(embeddings)
        
        # Compute t-SNE projection
        coordinates = self.compute_tsne_projection(embeddings_array)
        
        # Update attributes with coordinates
        for i, attr in enumerate(attributes_with_embeddings):
            if i < len(coordinates):
                setattr(attr, x_field, float(coordinates[i, 0]))
                setattr(attr, y_field, float(coordinates[i, 1]))
                attr.save(update_fields=[x_field, y_field])
                stats['projected'] += 1
            else:
                stats['skipped'] += 1
        
        logger.info(f"Projected {stats['projected']} {embedding_type} embeddings")
        return stats
    
    def get_projection_data_for_visualization(self, 
                                            project: Project,
                                            embedding_type: str = 'name') -> pd.DataFrame:
        """
        Prepare data for visualization with Plotly.
        
        Args:
            project: Project instance
            embedding_type: Type of embedding to visualize ('name' or 'description')
            
        Returns:
            DataFrame formatted for Plotly visualization
        """
        # Get attributes with projections
        attributes = Attribute.objects.filter(
            studies__project=project
        ).distinct()
        
        # Build DataFrame for visualization
        data = []
        x_field = f"{embedding_type}_tsne_x"
        y_field = f"{embedding_type}_tsne_y"
        
        for attr in attributes:
            x_coord = getattr(attr, x_field, None)
            y_coord = getattr(attr, y_field, None)
            
            if x_coord is not None and y_coord is not None:
                # Get the study this attribute belongs to
                study = attr.studies.filter(project=project).first()
                study_name = study.name if study else 'Unknown Study'
                
                data.append({
                    'id': attr.id,
                    'variable_name': attr.variable_name,
                    'display_name': attr.display_name or attr.variable_name,
                    'description': attr.description or '',
                    'category': attr.category,
                    'variable_type': attr.variable_type,
                    'source_type': attr.source_type,
                    'unit': attr.unit or '',
                    'study_name': study_name,
                    'x': float(x_coord),
                    'y': float(y_coord),
                    'text': f"{attr.display_name or attr.variable_name}: {attr.description or 'No description'}",
                })
        
        df = pd.DataFrame(data)
        
        if df.empty:
            logger.warning(f"No projection data available for project {project.name}")
            return df
        
        # Add some metadata
        df['project_name'] = project.name
        df['embedding_type'] = embedding_type
        
        logger.info(f"Prepared {len(df)} data points for visualization")
        return df
    
    def compute_and_update_all_projections(self, project: Project) -> Dict[str, int]:
        """
        Compute and update all t-SNE projections for a project.
        
        Args:
            project: Project instance
            
        Returns:
            Dictionary with comprehensive statistics
        """
        logger.info(f"Starting t-SNE projection computation for project: {project.name}")
        
        stats = self.project_attributes_by_project(
            project=project,
            embedding_type="both",
        )
        
        logger.info(f"Completed t-SNE projection computation for project: {project.name}")
        return stats


# Global instance for easy import
tsne_service = TSNEProjectionService()