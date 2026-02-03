"""
Embedding service for generating and managing vector embeddings using OpenAI.
"""
import logging
import numpy as np
import tiktoken
from typing import List, Optional, Tuple
from django.conf import settings

logger = logging.getLogger(__name__)


class EmbeddingServiceError(Exception):
    """Exception raised when embedding service encounters an error."""
    pass


class EmbeddingQuotaError(EmbeddingServiceError):
    """Exception raised when OpenAI API quota is exceeded."""
    pass


class EmbeddingService:
    """Service for generating and managing embeddings using OpenAI's API."""
    
    def __init__(self):
        """Initialize the embedding service with lazy OpenAI client initialization."""
        self._client = None
        self._encoding = None
        self._initialized = False
    
    def _ensure_initialized(self):
        """Ensure the service is properly initialized with API key."""
        if self._initialized:
            return
            
        if not settings.OPENAI_API_KEY:
            raise EmbeddingServiceError(
                "OpenAI API key is not configured. Please set the OPENAI_API_KEY "
                "environment variable to enable AI-powered embedding generation."
            )
        
        from openai import OpenAI
        self._client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self._encoding = self._get_encoding()
        self._initialized = True
    
    @property
    def client(self):
        """Get the OpenAI client, initializing if needed."""
        self._ensure_initialized()
        return self._client
    
    @property
    def encoding(self):
        """Get the tokenizer encoding, initializing if needed."""
        self._ensure_initialized()
        return self._encoding
    
    @property
    def model(self):
        """Get the embedding model name from settings."""
        return settings.OPENAI_EMBEDDING_MODEL
    
    @property
    def max_tokens(self):
        """Get the max tokens from settings."""
        return settings.EMBEDDING_CHUNK_TOKENS
    
    @property
    def chunk_overlap(self):
        """Get the chunk overlap from settings."""
        return settings.EMBEDDING_CHUNK_OVERLAP
    
    def _get_encoding(self) -> tiktoken.Encoding:
        """Get the appropriate tokenizer encoding for the model."""
        try:
            # Use cl100k_base for GPT-3.5 and GPT-4 models
            return tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.warning(f"Could not load tiktoken encoding: {e}. Using fallback.")
            # Fallback to a basic encoding if the specific one fails
            return tiktoken.get_encoding("p50k_base")
    
    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in the given text."""
        if not text:
            return 0
        try:
            return len(self.encoding.encode(text))
        except Exception as e:
            logger.warning(f"Error counting tokens: {e}. Using character-based estimate.")
            # Fallback: rough estimate of 4 characters per token
            return len(text) // 4
    
    def chunk_text(self, text: str, max_tokens: Optional[int] = None, overlap: Optional[int] = None) -> List[str]:
        """
        Split text into chunks that fit within token limits.
        
        Args:
            text: The text to chunk
            max_tokens: Maximum tokens per chunk (defaults to service setting)
            overlap: Number of tokens to overlap between chunks (defaults to service setting)
        
        Returns:
            List of text chunks
        """
        if not text or not text.strip():
            return [""]
        
        max_tokens = max_tokens or self.max_tokens
        overlap = overlap or self.chunk_overlap
        
        try:
            tokens = self.encoding.encode(text)
            
            if len(tokens) <= max_tokens:
                return [text]
            
            chunks = []
            start = 0
            
            while start < len(tokens):
                end = min(start + max_tokens, len(tokens))
                chunk_tokens = tokens[start:end]
                chunk_text = self.encoding.decode(chunk_tokens)
                chunks.append(chunk_text)
                
                if end >= len(tokens):
                    break
                
                # Move start position back by overlap amount for next chunk
                start = max(start + 1, end - overlap)
            
            return chunks if chunks else [""]
            
        except Exception as e:
            logger.error(f"Error chunking text: {e}. Returning original text.")
            return [text]
    
    def generate_embedding(self, text: str) -> Optional[np.ndarray]:
        """
        Generate an embedding for the given text.
        
        Args:
            text: The text to embed
            
        Returns:
            Numpy array of embedding values, or None if generation fails
        """
        if not text or not text.strip():
            return None
        
        try:
            # Clean and prepare text
            clean_text = text.strip()
            
            # Check if we need to chunk the text
            if self.count_tokens(clean_text) > self.max_tokens:
                chunks = self.chunk_text(clean_text)
                embeddings = []
                
                for chunk in chunks:
                    if chunk.strip():
                        response = self.client.embeddings.create(
                            model=self.model,
                            input=chunk
                        )
                        embedding = np.array(response.data[0].embedding, dtype=np.float32)
                        embeddings.append(embedding)
                
                if embeddings:
                    # Average the embeddings from all chunks
                    final_embedding = np.mean(embeddings, axis=0)
                    # Normalize the final embedding
                    return self._normalize_vector(final_embedding)
                else:
                    return None
            else:
                # Single chunk processing
                response = self.client.embeddings.create(
                    model=self.model,
                    input=clean_text
                )
                embedding = np.array(response.data[0].embedding, dtype=np.float32)
                return self._normalize_vector(embedding)
                
        except Exception as e:
            error_str = str(e).lower()
            # Check for quota/rate limit errors
            if 'insufficient_quota' in error_str or 'exceeded your current quota' in error_str:
                logger.error(f"OpenAI API quota exceeded: {e}")
                raise EmbeddingQuotaError(
                    "OpenAI API quota exceeded. Please check your plan and billing details at "
                    "https://platform.openai.com/account/billing"
                )
            elif '429' in error_str or 'rate limit' in error_str:
                logger.error(f"OpenAI API rate limit: {e}")
                raise EmbeddingQuotaError(
                    "OpenAI API rate limit reached. Please wait a moment and try again, "
                    "or check your account quota."
                )
            logger.error(f"Error generating embedding: {e}")
            return None
    
    def _normalize_vector(self, vector: np.ndarray) -> np.ndarray:
        """Normalize a vector to unit length (L2 normalization)."""
        norm = np.linalg.norm(vector)
        if norm == 0:
            return vector
        return vector / norm
    
    def generate_attribute_embeddings(self, variable_name: str, description: str = "") -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
        """
        Generate embeddings for an attribute's name and description.
        
        Args:
            variable_name: The variable name to embed
            description: The variable description to embed (optional)
            
        Returns:
            Tuple of (name_embedding, description_embedding)
        """
        # Preprocess variable name
        clean_name = self._preprocess_variable_name(variable_name)
        name_embedding = self.generate_embedding(clean_name)
        
        # Generate description embedding if description is provided
        description_embedding = None
        if description and description.strip():
            clean_description = description.strip()
            description_embedding = self.generate_embedding(clean_description)
        
        return name_embedding, description_embedding
    
    def _preprocess_variable_name(self, variable_name: str) -> str:
        """
        Preprocess variable name to improve embedding quality.
        
        Args:
            variable_name: Raw variable name
            
        Returns:
            Preprocessed variable name
        """
        if not variable_name:
            return ""
        
        # Convert to lowercase
        name = variable_name.lower().strip()
        
        # Replace underscores and hyphens with spaces
        name = name.replace("_", " ").replace("-", " ")
        
        # Simple medical abbreviation expansion
        abbreviations = {
            "bp": "blood pressure",
            "hr": "heart rate", 
            "bmi": "body mass index",
            "temp": "temperature",
            "wt": "weight",
            "ht": "height",
            "dob": "date of birth",
            "id": "identifier",
            "num": "number",
            "addr": "address",
            "dx": "diagnosis",
            "rx": "prescription",
            "pt": "patient",
            "hosp": "hospital",
            "admin": "admission",
            "discharge": "discharge",
            "lab": "laboratory",
            "med": "medication",
            "surg": "surgery",
            "proc": "procedure",
        }
        
        # Apply abbreviation expansions
        words = name.split()
        expanded_words = []
        for word in words:
            expanded_words.append(abbreviations.get(word, word))
        
        return " ".join(expanded_words)
    
    def validate_embedding_dimensions(self, embedding: Optional[np.ndarray]) -> bool:
        """
        Validate that an embedding has the expected dimensions.
        
        Args:
            embedding: The embedding to validate
            
        Returns:
            True if valid, False otherwise
        """
        if embedding is None:
            return False
        
        expected_dim = settings.EMBEDDING_DIMENSIONS
        
        if len(embedding) != expected_dim:
            logger.warning(f"Embedding dimension mismatch: expected {expected_dim}, got {len(embedding)}")
            return False
        
        # Check for NaN or infinite values
        if np.any(np.isnan(embedding)) or np.any(np.isinf(embedding)):
            logger.warning("Embedding contains NaN or infinite values")
            return False
        
        return True


# Global instance for easy import
embedding_service = EmbeddingService()