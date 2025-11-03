"""
API response models for the Pi calculation service.

This module defines Pydantic models used for HTTP responses in the Pi calculation
API endpoints. These models provide validation, serialization, and OpenAPI
documentation generation.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


class StartCalcResponse(BaseModel):
    """
    Response model for initiating a Pi calculation task.

    This model is returned when a new calculation is started, providing
    the task identifier needed to track the calculation's progress.

    Attributes:
        task_id: Unique identifier for the Celery background task that's
                 performing the calculation. Use this ID to query the
                 calculation progress via the status endpoint.

    """

    task_id: str = Field(..., description="Celery task identifier")


class ProgressResponse(BaseModel):
    """
    Response model for Pi calculation progress and results.

    This model represents the current state of a calculation task, including
    its progress and final result when complete. Used by the status/progress
    endpoint to inform clients about ongoing or finished calculations.

    Attributes:
        state: Current task state - either "PROGRESS" for ongoing calculations
               or "FINISHED" when complete.
        progress: Completion ratio as a float between 0.0 and 1.0, where
                  0.0 represents not started and 1.0 represents complete.
        result: The calculated value of Pi rounded to the requested number
                of digits. Only populated when state is "FINISHED", otherwise None.

    """

    state: Literal["PROGRESS", "FINISHED"] = Field(..., description="Task state")
    progress: float = Field(..., ge=0, le=1, description="Completion ratio")
    result: Optional[str] = Field(
        None, description="π value rounded to N digits when finished"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"state": "FINISHED", "progress": 1.0, "result": "3.14159"},
            ]
        }
    }
