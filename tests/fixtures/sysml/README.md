# SysML v2 fixtures

Short excerpts of the official SysML v2 example models, used by `tests/test_sysml.py` to check that the
importer reads real textual notation without crashing and reports what it cannot map as gaps.

Source: the [SysML v2 Release](https://github.com/Systems-Modeling/SysML-v2-Release) repository,
`sysml/src/examples/`, at commit `fb97b754f29588b8e9c7a35f370880cd15eb29e7` (2026-09-11, "Updated for
2026-08"). Each file starts with a comment naming its exact source URL.

License: the repository's README states that the SysML v2 models in it are licensed under the Eclipse
Public License 2.0 by their copyright holders (listed in that README). The license text is in
`LICENSE-EPL-2.0.txt` here. These excerpts are distributed under the same license. Changes from the
originals: a two- or three-line source comment at the top of every file, and in the two files marked
"Excerpt", the omission of the line ranges named in that comment (marked `// ... (lines elided)`) and
the closing braces this requires.

| file | source |
|---|---|
| `vehicle_definitions.sysml` | Vehicle Example/VehicleDefinitions.sysml |
| `vehicle_usages.sysml` | Vehicle Example/VehicleUsages.sysml |
| `hsuv_requirements.sysml` | Requirements Examples/HSUVRequirements.sysml |
| `vehicle_requirement_derivation.sysml` | Requirements Examples/VehicleRequirementDerivation.sysml |
| `requirement_test.sysml` | Simple Tests/RequirementTest.sysml |
| `variability_test.sysml` | Simple Tests/VariabilityTest.sysml |
| `verification_test.sysml` | Simple Tests/VerificationTest.sysml |
| `analysis_test.sysml` | Simple Tests/AnalysisTest.sysml |
| `part_test.sysml` | Simple Tests/PartTest.sysml |
| `state_test.sysml` | Simple Tests/StateTest.sysml |
| `calculation_test.sysml` | Simple Tests/CalculationTest.sysml |
| `constraint_test.sysml` | Simple Tests/ConstraintTest.sysml |
| `fuel_economy_requirements.sysml` | Analysis Examples/Vehicle Analysis Demo.sysml (excerpt: vehicle model, fuel economy requirements, analysis context) |
| `annex_a_requirements.sysml` | Vehicle Example/SysML v2 Spec Annex A SimpleVehicleModel.sysml (excerpt: requirement definitions and the requirements package) |
