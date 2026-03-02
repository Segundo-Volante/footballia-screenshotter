"""
Classifier factory — creates the right classifier based on provider name.

Usage:
    from backend.classifiers import create_classifier
    classifier = create_classifier("openai", task_dict, config_dict)
"""
from backend.classifiers.base import BaseClassifier


def create_classifier(provider: str, task: dict, config: dict) -> BaseClassifier:
    """
    Factory function.

    Args:
        provider: "openai", "gemini", or "manual"
        task: Full task template dict (from TaskManager)
        config: Full app config dict (from config.yaml)

    Returns:
        A BaseClassifier subclass instance.
    """
    provider = provider.lower().strip()

    if provider == "openai":
        from backend.classifiers.openai_classifier import OpenAIClassifier
        return OpenAIClassifier(task, config)

    elif provider == "gemini":
        from backend.classifiers.gemini_classifier import GeminiClassifier
        return GeminiClassifier(task, config)

    elif provider == "manual":
        from backend.classifiers.manual_classifier import ManualClassifier
        return ManualClassifier(task, config)

    else:
        raise ValueError(
            f"Unknown classifier provider: '{provider}'. "
            f"Valid options: openai, gemini, manual"
        )
