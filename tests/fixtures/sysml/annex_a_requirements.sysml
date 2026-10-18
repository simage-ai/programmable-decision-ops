// Source: https://github.com/Systems-Modeling/SysML-v2-Release/blob/fb97b754f29588b8e9c7a35f370880cd15eb29e7/sysml/src/examples/Vehicle%20Example/SysML%20v2%20Spec%20Annex%20A%20SimpleVehicleModel.sysml
// SysML v2 Release, Eclipse Public License 2.0 (see LICENSE-EPL-2.0.txt and README.md here).
// Excerpt: lines 375-402, 859-923 of the source; elisions marked.
        package RequirementDefinitions{
            requirement def MassRequirement{
                doc /*The actual mass shall be less than the required mass*/
                attribute massRequired:>ISQ::mass;
                attribute massActual:>ISQ::mass;
                require constraint {massActual<=massRequired}
            }
            requirement def ReliabilityRequirement{
                doc /*The actual reliability shall be greater than the required reliability*/
                attribute reliabilityRequired:Real;
                attribute reliabilityActual:Real;
                require constraint {reliabilityActual>=reliabilityRequired}
            }
            requirement def TorqueGenerationRequirement {
                doc /* The engine shall generate torque as a function of RPM as shown in Table 1. */
                subject generateTorque:ActionDefinitions::GenerateTorque;
            }
            requirement def DrivePowerOutputRequirement { 
                doc /* The engine shall provide a connection point to transfer torque to the transmission.*/
            }
            requirement def FuelEconomyRequirement {
                doc /* The vehicle shall maintain an average fuel economomy of at least x miles per gallon for the nominal 
                driving scenario */
                attribute actualFuelEconomy :> distancePerVolume;
                attribute requiredFuelEconomy :> distancePerVolume;
                require constraint {actualFuelEconomy >= requiredFuelEconomy}
            }
        }
// ... (lines elided)
            package Requirements{
                public import RequirementDerivation::*;
                public import ModelingMetadata::*; // incudes status info
                item marketSurvey;
                dependency from vehicleSpecification to marketSurvey;
                
                requirement vehicleSpecification{
                    subject vehicle:Vehicle;
                    requirement <'1'> vehicleMassRequirement: MassRequirement {
                        doc /* The total mass of the vehicle shall be less than or equal to the required mass.
                        Assume total mass includes a full tank of gas of 60 kg*/
                        attribute redefines massRequired=2000 [kg];                     
                        attribute redefines massActual default vehicle.dryMass + fuelMassActual;
                        attribute fuelMassActual:>ISQ::mass;
                        attribute fuelMassMax:>ISQ::mass = 60 [kg];
                        assume constraint {fuelMassActual==fuelMassMax}
                    }
                    
                    allocate vehicleMassRequirement to PartsTree::vehicle_b.mass;
                    
                    requirement <'2'> vehicleFuelEconomyRequirements{
                        doc /* fuel economy requirements group */
                        attribute assumedCargoMass:>ISQ::mass;
                        requirement <'2_1'> cityFuelEconomyRequirement:FuelEconomyRequirement{
                            redefines requiredFuelEconomy= 10 [km / L];
                            assume constraint {assumedCargoMass<=500 [kg]}
                        }
                        requirement <'2_2'> highwayFuelEconomyRequirement:FuelEconomyRequirement{
                            redefines requiredFuelEconomy= 12.75 [km / L];
                            assume constraint {assumedCargoMass<=500 [kg]}
                            
                            //StatusInfo is contained in ModelingMetadata library
                            // StatusKind has values for open, closed, tbd, tbr, tbd
                            @StatusInfo {
                                status = StatusKind::closed;     
                                originator = "Bob";
                                owner = "Mary";
                            }
                        }
                    }
                }
                requirement engineSpecification {
                    subject engine1:Engine;
                    requirement <'1'> engineMassRequirement: MassRequirement {
                        doc /* The total mass of the engine shall be less than or equal to the required mass.*/
                        attribute redefines massRequired=200 [kg];                     
                        attribute redefines massActual = engine1.mass;
                    }
                    requirement torqueGenerationRequirement : TorqueGenerationRequirement{
                        subject generateTorque default engine1.generateTorque;
                    }

                    requirement drivePowerOutputRequirement : DrivePowerOutputRequirement{
                        port torqueOutPort{
                            out torque:Torque;
                        }
                    }
                }
                // the engine mass requirement is derived from the vehicle mass requirement
                #derivation connection {
                    end #original ::> vehicleSpecification.vehicleMassRequirement;
                    end #derive ::> engineSpecification.engineMassRequirement;
                }

            }
