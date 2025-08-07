from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setup(
    name="allora-python-proto",
    version="0.1.0",
    description="Allora Network Python Protobuf Library",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Allora Network",
    author_email="dev@allora.network",
    url="https://github.com/allora-network/allora-python-proto",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
    python_requires=">=3.8",
    install_requires=[
        "betterproto>=2.0.0b7",
        "grpcio>=1.50.0",
        "grpcio-tools>=1.50.0",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0.0",
            "pytest-asyncio>=0.21.0",
            "black>=22.0.0",
            "isort>=5.0.0",
            "mypy>=1.0.0",
            "flake8>=5.0.0",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)
