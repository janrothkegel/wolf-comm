import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name='wolf_comm',
    version='0.0.50',
    author="Jan Rothkegel",
    author_email="jan.rothkegel@web.de",
    description="A package to communicate with Wolf SmartSet Cloud",
    long_description=long_description,
    package_data={"wolf_comm": ["py.typed"]},
    long_description_content_type="text/markdown",
    url="https://github.com/janrothkegel/wolf-comm",
    include_package_data=True,
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.14",
        "Operating System :: OS Independent",
        "License :: OSI Approved :: Apache Software License",
    ],
    python_requires=">=3.14",
    install_requires=[
        'aiohttp>=3.12.0',
        'httpx>=0.26.0',
        'lxml>=6.0.0',
        'pkce>=1.0.3',
        'shortuuid>=1.0.11'
    ]
)
