from setuptools import setup, find_packages

setup(
    name='claude-inc',
    version='0.1.0',
    packages=find_packages(),
    py_modules=['claude_inc'],
    install_requires=[],
    author='Himanshu Tewatia',
    description='Pre-execution AI Agent CFO and Spend Firewall SDK',
    long_description=open('README.md', encoding='utf-8').read(),
    long_description_content_type='text/markdown',
    url='https://github.com/Himanshu-tewatia0/claude-inc',
    classifiers=[
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
    ],
    python_requires='>=3.6',
)
