from os import path

from setuptools import setup

this_directory = path.abspath(path.dirname(__file__))
with open(path.join(this_directory, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="cbpi4_BrewStart",
    version="1.3.0.post11",
    description="CraftBeerPi4: heats the strike water at a set time and holds it until Next, + step status actors",
    author="CraftBeerPi Community",
    url="https://github.com/braubunker/cbpi4_BrewStart",
    include_package_data=True,
    package_data={
        "": ["*.txt", "*.rst", "*.yaml", "*.md"],
        "cbpi4_BrewStart": ["*", "*.txt", "*.rst", "*.yaml", "*.md"],
    },
    packages=["cbpi4_BrewStart"],
    # Kein install_requires auf cbpi/cbpi4 — das Paket kommt von der CBPi-Installation
    install_requires=[],
    long_description=long_description,
    long_description_content_type="text/markdown",
    license="GPLv3",
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Programming Language :: Python :: 3",
        "Topic :: Home Automation",
    ],
)