// The pdo vehicle trade study as a SysML v2 model: three light tactical vehicle configurations, their
// mandatory requirements, and the range analysis that computes operating range from fuel and economy.
// All numbers are synthetic, chosen to demonstrate the model, not vehicle performance estimates.
//
//   pdo sysml import examples/vehicle/sysml/vehicle.sysml --map examples/vehicle/sysml/mapping.yaml
//
// reproduces the baseline's alternatives, requirements and observations (examples/vehicle/baseline).
package VehicleTradeStudy {
    doc /* Light tactical vehicle configuration trade study (synthetic data). */

    private import ScalarValues::Real;
    private import ISQ::*;
    private import USCustomaryUnits::*;

    part def Vehicle {
        doc /* A light tactical vehicle configuration. */

        attribute tankCapacity :> ISQ::volume { doc /* Usable fuel capacity */ }
        attribute fuelEconomy { doc /* Fuel economy */ }
        attribute range :> ISQ::length { doc /* Operating range (computed by RangeModel) */ }
        // SysML's unit library has no currency, so cost is a Real in millions of US dollars.
        attribute unitCost : Real { doc /* Unit acquisition cost */ }
        attribute reliability : Real { doc /* Mission reliability */ }

        port fuelFill : FuelFillPort;
    }

    port def FuelFillPort;

    variation part vehicleConfiguration : Vehicle {
        doc /* The configurations the program is choosing among. */

        variant part configA {
            doc /* Configuration A (current production derivative) */
            attribute :>> tankCapacity = 40 [gal];
            attribute :>> fuelEconomy = 8.0 [mi/gal];
            attribute :>> unitCost = 1.00;
            attribute :>> reliability = 0.97;
        }
        variant part configB {
            doc /* Configuration B (extended-range hybrid) */
            attribute :>> tankCapacity = 45 [gal];
            attribute :>> fuelEconomy = 8.0 [mi/gal];
            attribute :>> unitCost = 1.20;
            attribute :>> reliability = 0.98;
        }
        variant part configC {
            doc /* Configuration C (low-cost commercial platform) */
            attribute :>> tankCapacity = 47.5 [gal];
            attribute :>> fuelEconomy = 8.0 [mi/gal];
            attribute :>> unitCost = 0.95;
            attribute :>> reliability = 0.94;
        }
    }

    requirement def ReliabilityRequirement {
        doc /* The actual reliability shall be greater than or equal to the required reliability. */
        attribute reliabilityRequired : Real;
        attribute reliabilityActual : Real;
        require constraint { reliabilityActual >= reliabilityRequired }
    }

    requirement vehicleRequirements {
        doc /* Mandatory requirements every configuration must meet. */
        subject vehicle : Vehicle;

        requirement minRange {
            doc /* The vehicle shall have an operating range of at least 300 miles. */
            require constraint { vehicle.range >= 300 [mi] }
        }
        requirement minReliability : ReliabilityRequirement {
            doc /* The vehicle shall have a mission reliability of at least 0.95. */
            attribute :>> reliabilityRequired = 0.95;
            attribute :>> reliabilityActual = vehicle.reliability;
        }
        requirement maxUnitCost {
            doc /* The unit acquisition cost shall not exceed 1.40 million US dollars. */
            require constraint { vehicle.unitCost <= 1.40 }
        }
    }

    analysis def RangeModel {
        doc /* Operating range: usable fuel times fuel economy, derated for terrain. The computation is
             * implemented outside this model (examples/vehicle/evaluators.py, range_model). */
        subject vehicle : Vehicle;
        in tank_gal = vehicle.tankCapacity;
        in mpg = vehicle.fuelEconomy;
        in terrain_factor : Real default 1.0 { doc /* Terrain derate on range; 1.0 is the test route */ }
        return range_mi = vehicle.range;
    }
}
