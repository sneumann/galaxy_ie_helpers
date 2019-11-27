#!/usr/bin/env python
from setuptools import setup

setup(
    name = 'galaxy-ie-helpers',
    packages = ['galaxy_ie_helpers'],
    version = '0.2.4',
    description = "Helper scripts to work with Galaxy's Interactive Environments",
    author = 'Bjoern A. Gruening; Helena Rasche',
    author_email = 'bjoern.gruening@gmail.com',
    url = 'https://github.com/bgruening/galaxy_ie_helpers',
    license='LICENSE',
    keywords = ['Galaxy', 'Interactive Environments'],
    scripts=['bin/get', 'bin/put','bin/get_user_history'],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'Environment :: Console',
        "License :: OSI Approved :: MIT License",
        'Operating System :: POSIX',
        'Topic :: Software Development',
        'Topic :: Software Development :: Code Generators',
        'Natural Language :: English',
        "Programming Language :: Python :: 2",
        'Programming Language :: Python :: 2.7',
    ],
    install_requires=[
        "bioblend",
    ],
)
