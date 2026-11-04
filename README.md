<!-- Improved compatibility of back to top link: See: https://github.com/othneildrew/Best-README-Template/pull/73 -->
<a id="readme-top"></a>

<!--  *** Thanks for checking out the Best-README-Template. If you have a suggestion that would make this better, please fork the repo and create a pull request or simply open an issue with the tag "enhancement". Don't forget to give the project a star! Thanks again! Now go create something AMAZING! :D -->



<!-- /// d   u   b   p   i   x   e   l  ---  f   o   r   k   ////--v0.5.8 -->
<!--this has additionally been modifed by @dubpixel for hardware use -->
<!--search dpx_piprepper.. search & replace is COMMAND OPTION F -->

<!--this is the version for software -->
<!--sub-version of README-dpx_software_template.md: logo moved out of the top hero into a small footer block, hero slot swapped for a real product photo. field-tested in dpx_buttonode (commit f722407). see issue #1. -->
<!--todo ** new software product image? Remove it? -->
<!--igure out how to get the details tag to properly render in jekyll for gihub pages.-->



<!-- PROJECT SHIELDS -->
<!-- *** I'm using markdown "reference style" links for readability. Reference links are enclosed in brackets [ ] instead of parentheses ( ). See the bottom of this document for the declaration of the reference variables for contributors-url, forks-url, etc. This is an optional, concise syntax you may use. https://www.markdownguide.org/basic-syntax/#reference-style-links -->
<div align="center">

[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![License][license-shield]][license-url]
[![LinkedIn][linkedin-shield]][linkedin-url]
</div>
<div align="center">
<h1 align="center">dpx_piprepper</h1>
<h3 align="center"><i>cardbless: prep a stack of CompanionPi SD cards without touching a keyboard twice</i></h3>
  <p align="center">
    flash it, bless it, eject it, next card
    <br />
     »  
     <a href="https://github.com/dubpixel/dpx_piprepper"><strong>Project Here!</strong></a>
     »  
     <br />
    <a href="https://github.com/dubpixel/dpx_piprepper/issues/new?labels=bug&template=bug-report---.md">Report Bug</a>
    ·
    <a href="https://github.com/dubpixel/dpx_piprepper/issues/new?labels=enhancement&template=feature-request---.md">Request Feature</a>
    </p>
</div>

<div align="center">
  <img src="images/front.png" alt="dpx_piprepper hardware" width="600">
</div>

   <br />
<!-- TABLE OF CONTENTS -->
<details>
  <summary><h3>Table of Contents</h3></summary>
<ol>
    <li>
      <a href="#about-the-project">About The Project</a>
      <ul>
        <li><a href="#built-with">Built With</a></li>
      </ul>
    </li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
      </ul>
    </li>
    <li><a href="#usage">Usage</a></li>    
    <li><a href="#roadmap">Roadmap</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#contact">Contact</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
</ol>
</details>
<!-- ABOUT THE PROJECT -->
<details>
<summary><h3>About The Project</h3></summary>

**cardbless** is a macOS command-line tool that preps Raspberry Pi SD cards
running the CompanionPi image (Bitfocus Companion v5.x) for a show. Out of the
box, a fresh CompanionPi card has no admin user, no SSH, and no static IP; every
card needs the same manual first-boot dance repeated by hand. This tool does that
dance for you, and does it for a whole stack of cards in one sitting.

It is fully interactive (no flags, no config file): it walks you through a few
numbered menus and prompts, then offers to prep another card. Three jobs:

- **bless**: card is already flashed; write per-unit cloud-init files
  (`user-data`, `network-config`) to its boot partition so it comes up with a
  known admin user, SSH enabled, a static per-unit IP, and a unique hostname.
- **flash + bless**: write the CompanionPi image to the card first, then bless it.
- **clean up (over SSH)**: on a unit that has already booted, remove the leftover
  cloud-init `user-data` and `network-config` from its boot partition.

**Trunk mode (optional):** when blessing, you can also join one tagged 802.1Q
VLAN (default 218) alongside the untagged main network, either link-local
(self-assigned 169.254.x.x, what Dante uses) or a static IP. The main network
stays untagged on `eth0` so the card works on an access port or on a trunk whose
native VLAN is the main one.

Full spec lives in [`doc/CLAUDE.md`](doc/CLAUDE.md).
</br>

*author(s): // www.dubpixel.tv  - i@dubpixel.tv | other authors* 
</br>
<h3>Images</h3>

### FRONT
![FRONT][product-front]
</details>
<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Built With 
 
 * Python 3 (stdlib-first) + PyYAML
 * [Raspberry Pi Imager CLI](https://github.com/raspberrypi/rpi-imager) (primary flash backend)
 * macOS `diskutil`, `dd`, `xz`, `openssl` (fallback flash backend + card prep)

<p align="right">(<a href="#readme-top">back to top</a>)</p>
<!-- GETTING STARTED -->

## Getting Started

> **Status:** v0.2.0, working. Bless, flash + bless, and post-boot cleanup are
> implemented. The VLAN trunk option is new and still needs a boot test on a real
> card.

  ### Prerequisites
  * macOS (Apple Silicon primary; Linux is nice-to-have, not required)
  * Python 3 and PyYAML (`pip3 install pyyaml`)
  * [Raspberry Pi Imager](https://www.raspberrypi.com/software/) installed, if using flash mode
    (falls back to `dd` + `xz` if it is not found)
  * `openssl` with `-6` support for password hashing (macOS's system LibreSSL may
    lack this; Homebrew `openssl@3` is the fallback)

  ### Installation

  1. Clone this repo
  2. `pip3 install pyyaml`
  3. Run `python3 src/cardbless-v1.py`

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- USAGE EXAMPLES -->
## Usage

```sh
python3 src/cardbless-v1.py
```

There are no flags. The tool asks, in order:

1. What to do: flash + bless, bless an already-flashed card, or clean up a booted unit over SSH
2. *(flash only)* the image (a numbered list of every `.img` / `.img.xz` found in `~/Downloads` and its subfolders, or type a full path) and the target disk (external, physical, size-capped, with a typed confirmation before anything is erased)
3. Admin username, password (hashed with `openssl passwd -6`), and hostname
4. Static IP, prefix length and gateway, then optionally a tagged VLAN
5. A summary to confirm, then it writes, validates and ejects the card, and asks whether to prep another

Every blessed card is appended to `cardbless.log` (not committed).

_For the full spec, see [`doc/CLAUDE.md`](doc/CLAUDE.md)._
<!-- ROADMAP -->
## Roadmap

- [x] bless mode (write cloud-init files to an already-flashed card)
- [x] flash + bless mode (rpi-imager CLI primary, `dd` fallback)
- [x] post-boot cloud-init cleanup over SSH
- [x] optional tagged VLAN (trunk mode), link-local or static *(needs a real-card boot test)*
- [ ] batch mode from a show config (units list); today you re-enter details per card
- [ ] remote image listing from Bitfocus's stable release tree, defaulting to latest
- [ ] Nice-to-have: post-boot verify (ping unit, check Companion's web port)
- [ ] Nice-to-have: SSH public key auth alongside/instead of password
- [ ] Nice-to-have: Linux support (`lsblk` instead of `diskutil`)

See the [open issues](https://github.com/dubpixel/dpx_piprepper/issues) for a full list of proposed features (and known issues).

<!-- CONTRIBUTING -->
## Contributing

_Contributions are what make the open source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**._

If you have a suggestion that would make this better, please fork the repo and create a pull request. You can also simply open an issue with the tag "enhancement".
Don't forget to give the project a star! Thanks again!

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

### Top contributors:
<a href="https://github.com/dubpixel/dpx_piprepper/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=dubpixel/dpx_piprepper" alt="contrib.rocks image" />
</a>

<!-- LICENSE -->
## License
Distributed under the MIT License. See `LICENSE.txt` for more information.
<!-- CONTACT -->
## Contact

  ### Joshua Fleitell - i@dubpixel.tv

  Project Link: [https://github.com/dubpixel/dpx_piprepper](https://github.com/dubpixel/dpx_piprepper)

<!-- ACKNOWLEDGMENTS -->
## Acknowledgments

<!--
  * [ ]() - the best !
-->

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<div align="center">
  <a href="https://github.com/dubpixel/dpx_piprepper">
    <img src="images/logo.png" alt="dpx logo" height="48">
  </a>
  <br>
  <a href="https://github.com/dubpixel/dpx_piprepper/releases/latest">
    <img src="https://img.shields.io/github/v/release/dubpixel/dpx_piprepper?label=dpx_piprepper&color=blue&style=flat-square" alt="Latest Release">
  </a>
</div>

<!-- MARKDOWN LINKS & IMAGES -->
<!-- https://www.markdownguide.org/basic-syntax/#reference-style-links -->
[contributors-shield]: https://img.shields.io/github/contributors/dubpixel/dpx_piprepper.svg?style=flat-square
[contributors-url]: https://github.com/dubpixel/dpx_piprepper/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/dubpixel/dpx_piprepper.svg?style=flat-square
[forks-url]: https://github.com/dubpixel/dpx_piprepper/network/members
[stars-shield]: https://img.shields.io/github/stars/dubpixel/dpx_piprepper.svg?style=flat-square
[stars-url]: https://github.com/dubpixel/dpx_piprepper/stargazers
[issues-shield]: https://img.shields.io/github/issues/dubpixel/dpx_piprepper.svg?style=flat-square
[issues-url]: https://github.com/dubpixel/dpx_piprepper/issues
[license-shield]: https://img.shields.io/github/license/dubpixel/dpx_piprepper.svg?style=flat-square
[license-url]: https://github.com/dubpixel/dpx_piprepper/blob/main/LICENSE.txt
[linkedin-shield]: https://img.shields.io/badge/-LinkedIn-black.svg?style=flat-square&logo=linkedin&colorB=555
[linkedin-url]: https://linkedin.com/in/jfleitell
[product-front]: images/front.png
[product-rear]: images/rear.png
[product-front-rendering]: images/front_render.png
[product-rear-rendering]: images/rear_render.png
[product-pcbFront]: images/pcb_front.png
[product-pcbRear]: images/pcb_rear.png
[Next.js]: https://img.shields.io/badge/next.js-000000?style=for-the-badge&logo=nextdotjs&logoColor=white
[Next-url]: https://nextjs.org/
[React.js]: https://img.shields.io/badge/React-20232A?style=for-the-badge&logo=react&logoColor=61DAFB
[React-url]: https://reactjs.org/
[Vue.js]: https://img.shields.io/badge/Vue.js-35495E?style=for-the-badge&logo=vuedotjs&logoColor=4FC08D
[Vue-url]: https://vuejs.org/
[Angular.io]: https://img.shields.io/badge/Angular-DD0031?style=for-the-badge&logo=angular&logoColor=white
[Angular-url]: https://angular.io/
[Svelte.dev]: https://img.shields.io/badge/Svelte-4A4A55?style=for-the-badge&logo=svelte&logoColor=FF3E00
[Svelte-url]: https://svelte.dev/
[Laravel.com]: https://img.shields.io/badge/Laravel-FF2D20?style=for-the-badge&logo=laravel&logoColor=white
[Laravel-url]: https://laravel.com
[Bootstrap.com]: https://img.shields.io/badge/Bootstrap-563D7C?style=for-the-badge&logo=bootstrap&logoColor=white
[Bootstrap-url]: https://getbootstrap.com
[JQuery.com]: https://img.shields.io/badge/jQuery-0769AD?style=for-the-badge&logo=jquery&logoColor=white
[JQuery-url]: https://jquery.com 
[KiCad.org]: https://img.shields.io/badge/KiCad-v8.0.6-blue
[KiCad-url]: https://kicad.org 
[Fusion-360]: https://img.shields.io/badge/Fusion360-v4.2.0-green
[Autodesk-url]: https://autodesk.com 
[FastLed.io]: https://img.shields.io/badge/FastLED-v3.9.9-red
[FastLed-url]: https://fastled.io 
