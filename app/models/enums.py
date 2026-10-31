from enum import Enum


class Environment(str, Enum):
    production = "production"
    staging = "staging"
    development = "development"


class ApplicationStatus(str, Enum):
    active = "active"
    maintenance = "maintenance"
    archived = "archived"
