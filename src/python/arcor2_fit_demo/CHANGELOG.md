# Changelog

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),

## [0.6.0] - 2021-05-21

## Changed

- Object types now use a mixin to share the common code.

## Fixed

- State controller paths are now consistent.

## [0.5.2] - 2021-03-30

## Fixed
- Dobot M1 does not have IK/FK so far

## [0.5.1] - 2021-03-15

### Fixed
- Signatures of some robot methods.
- Added temporal workaround to `AbstractDobot/pick`.

## [0.5.0] - 2021-03-03

### Changed
- `pick`/`place` actions.
- Hand teaching mode.
- During initialization, stop the Dobot service if already started.
- Raise exception on `safe=True` (not supported).

## [0.4.0] - 2021-02-08

### Changed
- ObjectTypes are now just proxies to the Dobot service.
- Added the mandatory `an` parameter to all actions.

### Fixed
- Dobot M1 model (rotation of parts).

## [0.3.0] - 2020-12-14

### Changed
- Various fixes regarding `DobotMagician` and `DobotM1`.
- `KinectAzure` ObjectType was added.

## [0.2.1] - 2020-10-19

### Changed
- ARCOR2 dependency updated

## [0.2.0] - 2020-09-24
### Changed
- Package is now part of the arcor2 monorepo.
- Support for Dobot M1.
- Improved handling of exceptions.