"""The engine plant (plant.py): the program's own air table read from the
image, the orifice model standing in for it, and the torque the fuel and
spark make."""
import pytest

from ms43emu import find_image, load_image, plant


def test_the_orifice_model_fills_more_with_angle_and_less_with_speed():
    a = plant.Air()
    assert a.mg(0, 800) < a.mg(2, 800) < a.mg(10, 800) < a.mg(85, 800)
    assert a.mg(2, 800) > a.mg(2, 3000)                    # the same opening starves a faster pump
    assert 0.9 < plant.manifold_kpa(85, 3000) / plant.AMBIENT_KPA <= 1.0
    assert plant.manifold_kpa(0, 800) < 0.3 * plant.AMBIENT_KPA
    assert abs(a.fraction(85, 3000) - 1.0) < 1e-6


def test_the_table_from_the_image_agrees_with_the_program():
    if find_image("stock") is None:
        pytest.skip("no stock image")
    a = plant.Air.from_image(load_image("stock"))
    degs, rpms, rows = a.table
    assert degs[0] == 0 and 69 < degs[-1] < 71 and rpms[0] == 416 and rpms[-1] == 6496
    # the program's own numbers: 20 kg/h at 800 rpm is about 137 mg a filling (test_sensors reads 137)
    assert abs(plant.air_mg_per_stroke(20.0, 800) - 137) < 3
    # a closed plate starves a fast engine, a wide open one fills it, and the share runs 0..1
    assert a.mg(0, 6000) < 15 and a.mg(85, 3000) > 400
    assert 0 < a.fraction(2, 800) < a.fraction(5, 800) < a.fraction(85, 800) == 1.0
    # kg/h and mg agree
    assert abs(plant.air_mg_per_stroke(a.kgh(10, 2000), 2000) - a.mg(10, 2000)) < 1e-6


def test_the_fuel_fit_reproduces_the_programs_idle_and_full_load():
    # 137 mg at 2.77 ms is stoichiometric; 458 mg at 9.2 ms is rich as a full load is
    lam_idle = 137 / (plant.STOICH * plant.fuel_mg(2.77))
    lam_wot = 458 / (plant.STOICH * plant.fuel_mg(9.2))
    assert abs(lam_idle - 1.0) < 0.05
    assert 0.8 < lam_wot < 0.95


def test_torque_has_the_m54s_shape():
    a = plant.Air()
    full_3000 = a.mg(85, 3000)
    wot = plant.torque_nm(full_3000, 3000, 9.2, 24.8, 90.0, full_3000)
    assert 200 < wot < 270                                 # an M54B25's 245 Nm, give or take the model
    assert plant.torque_nm(0, 3000, 0.0, 20.0, 90.0, full_3000) < 0        # no fuel: friction only
    assert plant.torque_nm(130, 800, 2.7, 8.0, 90.0, a.mg(85, 800)) > 0     # a warm idle makes a little
    # spark away from MBT costs torque, cold friction costs more
    assert plant.ignition_efficiency(25, 1.0) > plant.ignition_efficiency(5, 1.0)
    assert plant.friction_nm(800, -10) > plant.friction_nm(800, 90)
