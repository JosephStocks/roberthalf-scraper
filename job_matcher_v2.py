# filename: job_matcher_v2.py
import json
import logging
import time
from datetime import UTC, datetime  # Use UTC alias
from pathlib import Path
from typing import Any  # Use Dict and Optional

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

logger = logging.getLogger(__name__)

class JobMatchAnalyzerV2:
    """
    Analyzes job descriptions against a candidate profile using a two-tier approach.
    Tier 1: Fast skill scoring.
    Tier 2: Holistic analysis (skills context, experience, location, role).
    """

    # --- Tier 1 System Prompt (Skill Focused) ---
    TIER1_SYSTEM_PROMPT = """
You are a job skill matching assistant. Evaluate how well the skills listed in the job posting match the candidate's skills.

You will receive:
- A candidate profile (skills with levels: core=3, secondary=1, familiar=0.3)
- A job posting text

Evaluate the skill match ONLY and return a JSON object:
{
  "skill_score": number (0-100, weighted sum of matched skills / total possible weight),
  "keyword_matches": [list of candidate skill names found exactly in the job post (case-insensitive)],
  "semantic_matches": [list of candidate skill names conceptually relevant but not exact matches],
  "missing_core_skills": [list of candidate 'core' skills NOT found or implied]
}

Scoring Rules:
- Use skill weights: "core" = 3, "secondary" = 1, "familiar" = 0.3
- Exact keyword match (case-insensitive): 100% of weight.
- Conceptual/semantic match (clearly implied): 60% of weight.
- If unclear, do not score.
- Calculate skill_score as (sum of scores for matched skills) / (sum of weights for ALL candidate skills) * 100. Round to one decimal place.

Return ONLY valid JSON.
"""

    # --- Tier 2 System Prompt (Holistic Analysis) ---
    TIER2_SYSTEM_PROMPT = """
You are a senior job match analyzer. Given a candidate profile, job description, and preliminary skill analysis,
evaluate the overall fit across multiple dimensions.

Candidate Preferences Overview:
- Experience: Target 0-5 years (candidate has 3 years). Score lower for roles requiring significantly more (e.g., 6+ yrs).
- Location Preference Rating (1-10): Remote US (10), Hybrid TX (9), On-site TX (6), Outside TX (1).
- Use structured job metadata for location/type if provided at the end of the description.

Input You Receive:
- candidate_profile: JSON with skills, experience_years, preferred_titles, industries, location preferences detailed above.
- job_description: Text of the job posting (may include metadata).
- tier1_skill_analysis: JSON from a previous step containing skill_score, keyword_matches, semantic_matches.

Tasks:
1.  Review Tier 1 skill analysis for context, but focus on the broader picture.
2.  Evaluate Experience Level Match (1-10 score).
3.  Evaluate Location Match (1-10 score) based on candidate preferences and job details (use metadata if available).
4.  Evaluate Role Match (1-10 score) based on candidate's preferred titles and job duties.
5.  Evaluate Industry Match (optional, boolean or 1-10 score if applicable).
6.  Provide an overall recommendation and summary.

Format your response as JSON:
{
  "experience_match": {
    "score": number (1-10),
    "reasoning": "Brief explanation."
  },
  "location_match": {
    "score": number (1-10),
    "location_type": "remote|hybrid|on-site|unspecified",
    "location_detected": "Detected location or 'unspecified'",
    "reasoning": "Explanation based on candidate preferences."
  },
  "role_match": {
    "score": number (1-10),
    "reasoning": "Alignment with preferred titles/duties."
  },
  "industry_match": { // Optional field, include if relevant info found
    "score": number (1-10), // Or use boolean "match": true/false
    "reasoning": "Connection to candidate's industry experience."
  },
  "overall_recommendation": "apply|consider|skip", // Based on all factors
  "summary": "Concise (1-2 sentence) overall assessment of the job fit."
}

Return ONLY valid JSON. Do not repeat the skill list unless justifying the role match.
"""

    def __init__(self, config: dict[str, Any], llm_debug: bool = False): # Added llm_debug
        """
        Initialize the analyzer with configuration.

        Args:
            config (dict): Dictionary containing API keys, model names, thresholds, profile path.
            llm_debug (bool): Flag to enable verbose LLM logging.
        """
        self.config = config
        self.llm_debug = llm_debug # Store the flag
        if self.llm_debug:
            logger.info("LLM Debug logging enabled for JobMatchAnalyzerV2.")

        self.api_key = config.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("OpenAI API key not found in config.")

        self.client = OpenAI(api_key=self.api_key)
        # Ensure candidate_profile is loaded correctly based on the file structure
        profile_path_str = config.get("CANDIDATE_PROFILE_PATH")
        self.candidate_profile = self._load_profile(profile_path_str)

        # Store model names and thresholds
        self.model_tier1 = config.get('MATCHING_MODEL_TIER1', 'gpt-4o-mini')
        self.threshold_tier1 = config.get('MATCHING_THRESHOLD_TIER1', 60)
        self.model_tier2 = config.get('MATCHING_MODEL_TIER2', 'gpt-4o-mini') # Consistent model choice
        self.final_threshold = config.get('MATCHING_THRESHOLD_FINAL', 75) # Used later for filtering notifications

    def _load_profile(self, profile_path_str: str | None) -> dict[str, Any] | None: # Use Optional
        """Loads the candidate profile JSON."""
        if not profile_path_str:
            logger.error("Candidate profile path not configured.")
            return None
        profile_path = Path(profile_path_str)
        if not profile_path.exists():
            logger.error(f"Candidate profile file not found at: {profile_path}")
            return None
        try:
            with open(profile_path, encoding='utf-8') as f:
                # Load the JSON directly, assuming it starts with {"name": ...}
                profile = json.load(f)
            logger.info(f"Candidate profile loaded successfully from {profile_path}")
            # Add experience years if it wasn't top-level (adjust if needed based on final JSON structure)
            if 'experience_years' not in profile:
                # Try finding it nested if your structure is different
                # profile['experience_years'] = profile.get('candidate_profile', {}).get('experience_years', 3)
                 profile['experience_years'] = 3 # Default if truly missing
                 logger.warning("experience_years not found at top level, using default.")
            return profile
        except (OSError, FileNotFoundError, json.JSONDecodeError, Exception) as e: # Added FileNotFoundError
            logger.error(f"Failed to load or parse candidate profile from {profile_path}: {e}")
            return None

    def _call_openai_api(self, system_prompt: str, user_content: str, model: str, max_retries: int = 2, initial_delay: float = 5.0) -> dict[str, Any] | None: # Use Optional
        """Helper function to call OpenAI API with retries and JSON parsing."""
        if self.llm_debug:
            logger.debug(f"--- LLM Call Start ({model}) ---")
            logger.debug(f"System Prompt:\n{system_prompt}")
            # Avoid logging potentially huge user content unless necessary
            logger.debug(f"User Content (first 500 chars):\n{user_content[:500]}...")

        attempt = 0
        delay = initial_delay
        last_exception = None
        response_content = None # Initialize for error logging

        while attempt <= max_retries:
            attempt += 1
            try:
                logger.debug(f"Calling OpenAI API ({model}) - Attempt {attempt}")
                response = self.client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                    timeout=60.0 # Increased timeout for potentially complex analysis
                )
                response_content = response.choices[0].message.content
                if not response_content:
                    logger.warning(f"OpenAI API ({model}) returned empty content.")
                    # Consider if retry is useful here, maybe a temp API issue
                    last_exception = ValueError("API returned empty content")
                    if attempt <= max_retries:
                         sleep_time = delay * (2 ** (attempt - 1))
                         logger.info(f"Retrying {model} call due to empty response in {sleep_time:.1f} seconds...")
                         time.sleep(sleep_time)
                         continue # Go to next attempt
                    else:
                         break # Max retries reached
                if self.llm_debug:
                    logger.debug(f"LLM Raw Response ({model}):\n{response_content}")

                # Parse JSON
                result = json.loads(response_content)
                logger.debug(f"OpenAI API ({model}) call successful and parsed.")
                if self.llm_debug:
                    logger.debug(f"--- LLM Call End ({model}) ---")
                return result
            except json.JSONDecodeError as json_err:
                logger.error(f"Failed to parse JSON from {model}: {json_err}. Response: {response_content}")
                last_exception = json_err
                # Maybe retry once on JSON error? Sometimes it's a fluke.
                if attempt == 1: # Only retry once on first JSON error
                     logger.info(f"Retrying {model} call once due to JSON parse error...")
                     time.sleep(delay)
                     continue
                break # Don't retry JSON parsing errors further
            except (RateLimitError, APIConnectionError, APITimeoutError) as api_err:
                logger.warning(f"OpenAI API error ({type(api_err).__name__}) on attempt {attempt} for {model}: {api_err}")
                last_exception = api_err
                if attempt > max_retries:
                    logger.error(f"Max retries reached for {model}.")
                    break
                sleep_time = delay * (2 ** (attempt - 1)) # Exponential backoff
                logger.info(f"Retrying {model} call in {sleep_time:.1f} seconds...")
                time.sleep(sleep_time)
            except Exception as e:
                logger.error(f"Unexpected error during OpenAI API call ({model}): {e}", exc_info=True)
                last_exception = e
                break # Don't retry unexpected errors

        logger.error(f"Failed to get valid response from OpenAI ({model}) after {attempt} attempts. Last error: {last_exception}")
        if self.llm_debug:
             logger.debug(f"--- LLM Call End ({model}) - FAILED ---")
        return None

    def _run_tier1_analysis(self, job_description: str) -> dict[str, Any] | None: # Use Optional
        """Runs the Tier 1 skill analysis."""
        if not self.candidate_profile: return None
        if self.llm_debug:
            logger.debug("--- Running Tier 1 Analysis ---")

        user_content = json.dumps({
            "candidate_profile": {
                "skills": self.candidate_profile.get("skills", [])
            },
            "job_posting": job_description
        }, indent=2)

        # Debug log for user content already in _call_openai_api
        result = self._call_openai_api(self.TIER1_SYSTEM_PROMPT, user_content, self.model_tier1)

        if self.llm_debug:
            logger.debug(f"Tier 1 Parsed Result: {result}")
            logger.debug("--- Tier 1 Analysis End ---")
        return result

    def _run_tier2_analysis(self, job_description: str, tier1_result: dict[str, Any]) -> dict[str, Any] | None: # Use Optional
        """Runs the Tier 2 holistic analysis, using Tier 1 results."""
        if not self.candidate_profile: return None
        if self.llm_debug:
            logger.debug("--- Running Tier 2 Analysis ---")

        user_content = json.dumps({
            "candidate_profile": self.candidate_profile, # Full profile
            "job_description": job_description,
            "tier1_skill_analysis": tier1_result # Pass Tier 1 results
        }, indent=2)

        # Debug log for user content already in _call_openai_api
        result = self._call_openai_api(self.TIER2_SYSTEM_PROMPT, user_content, self.model_tier2)

        if self.llm_debug:
            logger.debug(f"Tier 2 Parsed Result: {result}")
            logger.debug("--- Tier 2 Analysis End ---")
        return result

    def analyze_job(self, job_data: dict[str, Any]) -> dict[str, Any]: # Return Dict, including potential errors
        """
        Performs the full two-tier analysis for a single job.

        Args:
            job_data (dict): The job dictionary from the scraper (must include 'description').

        Returns:
            dict: A dictionary containing the combined analysis results.
                  Includes keys like 'tier1_result', 'tier2_result', 'final_score_calculated',
                  'meets_final_threshold', 'analysis_timestamp', and potentially 'error'.
        """
        # Get timestamp at the beginning of analysis attempt
        analysis_timestamp = datetime.now(UTC).isoformat() # Use UTC

        if self.llm_debug:
            logger.debug(f"== Starting Analysis for Job ID: {job_data.get('unique_job_number', 'UnknownID')} ==")

        if not self.candidate_profile:
            logger.error("Cannot analyze job: Candidate profile not loaded.")
            return {"error": "Profile not loaded", "analysis_timestamp": analysis_timestamp}

        job_description = job_data.get("description")
        job_id = job_data.get("unique_job_number", "N/A")
        job_title = job_data.get("jobtitle", "N/A")

        if not job_description:
            logger.warning(f"Skipping analysis for Job ID {job_id} ({job_title}): Missing description.")
            return {"error": "Missing description", "analysis_timestamp": analysis_timestamp}

        logger.info(f"--- Analyzing Job ID: {job_id} ({job_title}) ---")

        # --- Run Tier 1 ---
        tier1_result = self._run_tier1_analysis(job_description)

        if not tier1_result or 'skill_score' not in tier1_result:
            logger.error(f"Tier 1 analysis failed for Job ID {job_id}.")
            return { # Return partial info with error
                "error": "Tier 1 analysis failed",
                "tier1_result": tier1_result,
                "tier2_result": None,
                "final_score_calculated": None,
                "meets_final_threshold": False,
                "analysis_timestamp": analysis_timestamp
            }

        skill_score = tier1_result.get('skill_score', 0.0)
        logger.info(f"Job ID {job_id} - Tier 1 Skill Score: {skill_score:.1f}")

        # --- Check Tier 1 Threshold ---
        if skill_score < self.threshold_tier1:
            logger.info(f"Job ID {job_id} did not meet Tier 1 threshold ({self.threshold_tier1}). Skipping Tier 2.")
            return { # Return only Tier 1 info, no error key needed here
                "tier1_result": tier1_result,
                "tier2_result": None,
                "final_score_calculated": None,
                "meets_final_threshold": False,
                "analysis_timestamp": analysis_timestamp
            }

        # --- Run Tier 2 ---
        logger.info(f"Job ID {job_id} meets Tier 1 threshold. Proceeding to Tier 2 analysis.")
        time.sleep(1.0) # Small delay between API calls
        tier2_result = self._run_tier2_analysis(job_description, tier1_result)

        if not tier2_result:
            logger.error(f"Tier 2 analysis failed for Job ID {job_id}.")
            # Return Tier 1 info and Tier 2 failure indication
            return {
                "error": "Tier 2 analysis failed",
                "tier1_result": tier1_result,
                "tier2_result": None,
                "final_score_calculated": None,
                "meets_final_threshold": False,
                 "analysis_timestamp": analysis_timestamp
            }

        logger.info(f"Job ID {job_id} - Tier 2 Analysis Complete. Recommendation: {tier2_result.get('overall_recommendation', 'N/A')}")

        # --- Calculate Final Score (Using Tier 2 component scores) ---
        weights = { # Make weights configurable if needed
            "skill": 0.40,
            "experience": 0.25,
            "location": 0.20,
            "role": 0.15
        }
        s_score = skill_score / 10.0 # Normalize Tier 1 score (0-100 -> 0-10)
        e_score = tier2_result.get("experience_match", {}).get("score", 0)
        l_score = tier2_result.get("location_match", {}).get("score", 0)
        r_score = tier2_result.get("role_match", {}).get("score", 0)

        # Ensure scores are numeric before calculation
        try:
             calculated_score_10 = (
                 float(s_score) * weights["skill"] +
                 float(e_score) * weights["experience"] +
                 float(l_score) * weights["location"] +
                 float(r_score) * weights["role"]
             )
             final_score_calculated = round(calculated_score_10 * 10.0, 1) # Scale back to 0-100
             logger.info(f"Job ID {job_id} - Calculated Final Score: {final_score_calculated:.1f}")
        except (ValueError, TypeError) as calc_err:
             logger.error(f"Error calculating final score for Job ID {job_id}: {calc_err}. Scores: s={s_score}, e={e_score}, l={l_score}, r={r_score}")
             final_score_calculated = None # Indicate calculation failure

        # --- Determine if it meets the final threshold ---
        meets_final_threshold = False
        if final_score_calculated is not None:
            meets_final_threshold = final_score_calculated >= self.final_threshold
            if not meets_final_threshold:
                logger.info(f"Job ID {job_id} - Score ({final_score_calculated:.1f}) is below final threshold ({self.final_threshold}).")
        else:
             logger.warning(f"Cannot determine final threshold match for Job ID {job_id} due to score calculation error.")

        # Combine all results into the final analysis dictionary
        full_analysis = {
            "tier1_result": tier1_result,
            "tier2_result": tier2_result,
            "final_score_calculated": final_score_calculated,
            "meets_final_threshold": meets_final_threshold,
            "analysis_timestamp": analysis_timestamp # Use timestamp from start of analysis
        }

        if self.llm_debug:
            logger.debug(f"Tier 1 Score: {skill_score:.1f}")
            logger.debug(f"Tier 2 Scores: {e_score}, {l_score}, {r_score} -> Avg (0-100): {calculated_score_10:.1f}")
            logger.debug(f"Final Calculated Score: {final_score_calculated:.1f}")
            logger.debug(f"== Analysis End for Job ID: {job_id} ==")

        return full_analysis # Always return the analysis dict
