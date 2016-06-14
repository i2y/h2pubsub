#!/usr/bin/env python
from setuptools import setup
from pip.download import PipSession
from pip.req import parse_requirements


install_reqs = parse_requirements('requirements.txt', session=PipSession())
install_requires = [str(ir.req) for ir in install_reqs]

setup(
    name='h2pubsub',
    version='0.0.1',
    description='PUB/SUB',
    # long_description=readme,
    license='MIT License',
    author='Yasushi Itoh',
    url='https://github.com/i2y/h2pubsub',
    platforms=['any'],
    py_modules=['h2pubsub'],
    install_requires=install_requires,
    classifiers=[
        "Development Status :: 3 - Pre-Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.5",
        "Programming Language :: Python :: Implementation :: CPython",
        "Topic :: Software Development :: Libraries :: Python Modules"
    ]
)