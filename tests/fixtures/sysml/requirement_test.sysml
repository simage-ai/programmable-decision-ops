// Source: https://github.com/Systems-Modeling/SysML-v2-Release/blob/fb97b754f29588b8e9c7a35f370880cd15eb29e7/sysml/src/examples/Simple%20Tests/RequirementTest.sysml
// SysML v2 Release, Eclipse Public License 2.0 (see LICENSE-EPL-2.0.txt and README.md here).
package RequirementTest {
	constraint def C;
	constraint c : C;
	private import q::**;
	requirement def R {
		assume constraint c1 : C;
		require c;
		doc /* */
    	requirement;
    	requirement def <'1'> A {
    		doc /* Text */
    		subject s;
    	}
	}
	requirement def R1 {
		require constraint c1 :>> c;
	}
	part p;
	part q {
		requirement r : R;
		satisfy r by p;
		assert satisfy r by q;
	}
	
	requirement r1 : R1;
	not satisfy r1 by p;
	assert not satisfy r1 by q;
	
	constraint c1;
	constraint c2;
	concern c3;
	
	requirement def R2 {
		assume c1 [0..*];
		require c2 [0..*];
		frame c3[0..*];
	}
}
