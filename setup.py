from setuptools import setup, find_packages

setup(
    name="claude-inc",
    version="0.1.0",
    packages=find_packages(),
    py_modules=["claude_inc", "cli"],
    install_requires=[],
    entry_points={
        "console_scripts": [
            "claude-inc=cli:main",
        ],
    },
    author="Himanshu Tewatia",
    description="Pre-Execution AI Agent Spend Firewall & Token Ledger",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    url="https://github.com/Himanshu-tewatia0/claude_inc",
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires=">=3.8",
)