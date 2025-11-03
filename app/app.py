"""
Pi-as-a-Service: FastAPI application for computing π to arbitrary precision.

This module implements a REST API service that calculates π to a specified number
of digits using the Chudnovsky algorithm. Calculations are performed asynchronously
using Celery workers, allowing for long-running computations without blocking the API.

The service provides two main endpoints:
    - /calculate_pi: Initiates a new calculation task
    - /check_progress: Polls the status and retrieves results

Architecture:
    - FastAPI: Handles HTTP requests and responses
    - Celery: Manages background task execution
    - Redis: Acts as message broker and result backend
    - mpmath: Provides arbitrary-precision arithmetic
"""

import os
from typing import Dict, Any
from models import StartCalcResponse, ProgressResponse
from fastapi import FastAPI, HTTPException, Query
from celery import Celery
from mpmath import mp

# Constants

# Broker and backend URLs from environment variables or defaults
BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
"""str: Redis URL for Celery message broker. Configurable via CELERY_BROKER_URL env var."""

RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
"""str: Redis URL for storing task results. Configurable via CELERY_RESULT_BACKEND env var."""

# Chudnovsky algorithm constants
C = 640320
"""int: Base constant for Chudnovsky algorithm (640320)."""

C3_OVER_24 = (C**3) // 24
"""int: Precomputed value of C³/24 for optimization in term calculations."""


# Initialize Celery

celery_app = Celery(
    "pi_tasks",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
)
"""Celery: Application instance for managing background π calculation tasks."""

celery_app.conf.update(
    task_track_started=True,  # Enable progress tracking
    result_expires=3600,  # Results expire after 1 hour
)


# Initialize FastAPI

app = FastAPI(
    title="Pi-as-a-Service",
    version="1.1.0",
    description="Compute π to N digits using the Chudnovsky algorithm.",
    docs_url="/docs",
    redoc_url="/redoc",
)
"""FastAPI: Main application instance serving the π calculation API."""


def chudnovsky_term_calculation(k: int):
    """
    Calculate the k-th term of the Chudnovsky algorithm.

    The Chudnovsky algorithm uses binary splitting to efficiently compute π.
    Each term contributes to the final calculation through three components:
    P, Q, and T, which are combined using the binary splitting method.

    Args:
        k: Term index (0-based). The k=0 term uses special initialization values.

    Returns:
        tuple[int, int, int]: A tuple (P, Q, T) where:
            - P: Numerator product component
            - Q: Denominator product component
            - T: Numerator sum component (alternates sign for odd k)
    """
    if k == 0:
        P = 1
        Q = 1
        T = 13591409
    else:
        P = (6 * k - 5) * (2 * k - 1) * (6 * k - 1)
        Q = k * k * k * C3_OVER_24
        T = P * (13591409 + 545140134 * k)
        if k % 2:
            T = -T
    return P, Q, T


@celery_app.task(bind=True)
def calc_pi(
    self, digits: int = Query(..., description="Number of digits to calculate")
) -> str:
    """
    Calculate π to the specified number of digits using the Chudnovsky algorithm.

    This is a Celery task that performs the actual π calculation in the background.
    It uses the Chudnovsky algorithm with binary splitting for efficient computation
    of arbitrary-precision π values. Progress is reported periodically via Celery's
    state update mechanism.

    Args:
        self: Celery task instance (automatically bound when bind=True)
        digits: Number of decimal digits to calculate. Must be >= 0.
                digits=0 returns "3." as a special case.

    Returns:
        str: String representation of π rounded to the requested precision.
             Format: "3.14159..." with exactly `digits` total digits.

    Raises:
        Any exceptions from mpmath operations are propagated to Celery.

    Progress Updates:
        The task updates its state periodically with metadata:
        - state: "PROGRESS"
        - meta: {"current": current_term, "total": terms_needed}
    """

    if digits == 0:
        self.update_state(state="PROGRESS", meta={"current": 0, "total": 0})
        return "3."

    # extra guard digits to curb roundoff during intermediate steps
    mp.dps = digits + 25

    # estimate number of terms required
    terms_needed = int(digits / 14.181647462725477) + 1

    # Accumulators for (P, Q, T) combined sequentially
    P_total, Q_total, T_total = 1, 1, 0

    for curr_term in range(terms_needed):
        self.update_state(
            state="PROGRESS", meta={"current": curr_term, "total": terms_needed}
        )

        Pk, Qk, Tk = chudnovsky_term_calculation(curr_term)

        # Combine the current term with the total using the binary splitting method
        T_total = T_total * Qk + P_total * Tk
        P_total *= Pk
        Q_total *= Qk
    self.update_state(
        state="PROGRESS", meta={"current": terms_needed, "total": terms_needed}
    )

    pi = (Q_total * 426880 * mp.sqrt(10005)) / T_total

    # Round to requested precision
    mp.dps = digits
    result_str = mp.nstr(pi, n=digits)
    return result_str


@app.get(
    "/calculate_pi",
    response_model=StartCalcResponse,
    summary="Start a π calculation",
    description="Queues a background task to compute π to **n** digits.",
)
async def start_calc(n: int = Query(..., ge=0, description="Digits of π to compute")):
    """
    Start a background task to calculate π to n digits.

    This endpoint queues a new Celery task to compute π using the Chudnovsky
    algorithm. The calculation runs asynchronously, allowing the API to remain
    responsive during long computations. Use the returned task_id to poll for
    progress and results via the /check_progress endpoint.

    Args:
        n: Number of decimal digits to calculate. Must be non-negative integer.
           - n=0 returns "3."
           - n=1 returns "3.1"
           - n=100 returns π to 100 digits

    Returns:
        StartCalcResponse: Contains the Celery task_id for tracking the calculation.
    """
    try:
        task = calc_pi.delay(int(n))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"task_id": task.id}


@app.get(
    "/check_progress",
    response_model=ProgressResponse,
    summary="Check task progress or fetch result",
    description="Polls the Celery task; returns progress and the final value.",
)
async def check_progress(task_id: str = Query(..., description="Celery task id")):
    """
    Check the progress of a π calculation task.

    This endpoint polls a Celery task to retrieve its current state. For tasks
    in progress, it returns the completion percentage. For finished tasks, it
    returns the final calculated value of π.

    Args:
        task_id: The Celery task identifier returned by /calculate_pi endpoint.
                 Format: UUID string (e.g., "a1b2c3d4-e5f6-7890-abcd-ef1234567890")

    Returns:
        ProgressResponse: Contains state, progress ratio (0.0-1.0), and result.
            - state="PROGRESS": Task is still running
            - state="FINISHED": Task completed successfully
            - progress: Float from 0.0 (not started) to 1.0 (complete)
            - result: String representation of π (only when FINISHED)
    """

    async_result = celery_app.AsyncResult(task_id)

    if async_result.state == "SUCCESS":
        return {
            "state": "FINISHED",
            "progress": 1.0,
            "result": async_result.result,
        }
    else:
        meta: Dict[str, Any] = async_result.info or {}
        current = int(meta.get("current", 0))
        total = int(meta.get("total", 0))
        progress = float(current) / float(total) if total else 0.0
        return {
            "state": "PROGRESS",
            "progress": round(progress, 2),
            "result": None,
        }
