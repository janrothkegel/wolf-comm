import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name='wolf_comm',
    version='0.0.22',
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
        "Operating System :: OS Independent",
        "License :: OSI Approved :: Apache Software License",
    ],
    install_requires=[
        'aiohttp',
        'httpx',
        'lxml',
        'pkce',
        'shortuuid'
    ]
)
