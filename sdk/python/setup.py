"""Package configuration for civiclens Python SDK."""

from setuptools import setup, find_packages

setup(
    name="civiclens",
    version="1.0.0",
    description="Python SDK for the CivicLens local government meeting intelligence API",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    author="CivicLens",
    author_email="support@civiclens.ai",
    url="https://github.com/civiclens/civiclens-python",
    project_urls={
        "Documentation": "https://docs.civiclens.ai",
        "API Reference": "https://api.civiclens.ai/docs",
        "Changelog": "https://github.com/civiclens/civiclens-python/blob/main/CHANGELOG.md",
    },
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "httpx>=0.24.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-asyncio>=0.21",
            "respx>=0.20",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: Other/Proprietary License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Typing :: Typed",
    ],
    keywords="civiclens government meetings api sdk",
)
