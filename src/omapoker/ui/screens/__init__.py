"""Every screen in one place, so the app can navigate by name."""

from .base import (
    BaseScreen,
    ChoiceScreen,
    ConfirmScreen,
    MessageScreen,
    PanelScreen,
    PrivacyScreen,
)
from .ladder import HotSeatSetupScreen, TablePickerScreen
from .menu import MainMenuScreen, ResumeScreen
from .onboarding import OnboardingScreen, ProfileScreen
from .panels import (
    AssistScreen,
    GlossaryScreen,
    HelpScreen,
    LessonsScreen,
    MusicScreen,
    ReviewScreen,
    SettingsScreen,
    StatsScreen,
)
from .table import TableScreen

__all__ = [
    "AssistScreen",
    "BaseScreen",
    "ChoiceScreen",
    "ConfirmScreen",
    "GlossaryScreen",
    "HelpScreen",
    "HotSeatSetupScreen",
    "LessonsScreen",
    "MainMenuScreen",
    "MessageScreen",
    "MusicScreen",
    "OnboardingScreen",
    "PanelScreen",
    "PrivacyScreen",
    "ProfileScreen",
    "ResumeScreen",
    "ReviewScreen",
    "SettingsScreen",
    "StatsScreen",
    "TablePickerScreen",
    "TableScreen",
]
