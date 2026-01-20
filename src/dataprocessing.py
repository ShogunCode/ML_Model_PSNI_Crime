import geopandas as gpd
import pandas as pd
import os

def _dissolve_ward_duplicates(wards):
    required = {"WardCode_w", "WARDNAME", "geometry"}
    if not required.issubset(wards.columns):
        return wards
    dupes = wards.duplicated(subset=["WardCode_w", "WARDNAME"], keep=False)
    if not dupes.any():
        return wards
    base = wards[["WardCode_w", "WARDNAME", "geometry"]].copy()
    dissolved = base.dissolve(by=["WardCode_w", "WARDNAME"], as_index=False)
    return dissolved

def clean_data(shapefile_path, crime_data_path, output_path):
    # load the ward boundaries shapefile - used to join the crime data - 
    wards = gpd.read_file(shapefile_path)

    # rename 'WardCode' in wards to avoid conflicts - issues with suffix previosuly 
    wards = wards.rename(columns={'WardCode': 'WardCode_w'})
    wards = _dissolve_ward_duplicates(wards)

    # load the crime data csv file
    crime_data = pd.read_csv(crime_data_path)

    # convert the crime data latitude and longitude to a GeoDataFrame
    crime_gdf = gpd.GeoDataFrame(
        crime_data,
        geometry=gpd.points_from_xy(crime_data['Longitude'], crime_data['Latitude']),
        crs="EPSG:4326"
    )

    # ensure the CRS matches
    if crime_gdf.crs is not None:
        wards = wards.to_crs(crime_gdf.crs)

    # perform a spatial join between the crime data and the wards data without suffixes
    crime_with_ward = gpd.sjoin(
        crime_gdf,
        wards,
        how="left",
        predicate="within"
    )

    # name 'WardCode_w' back to 'WardCode' for consistency
    crime_with_ward.rename(columns={'WardCode_w': 'WardCode'}, inplace=True)

    # drop unnecessary columns
    columns_to_keep = [
        'Month', 'Longitude', 'Latitude', 'Location',
        'Crime type', 'WARDNAME', 'WardCode', 'geometry'
    ]
    crime_with_ward = crime_with_ward[columns_to_keep]

    # export the result to a new file
    crime_with_ward.to_csv(output_path, index=False)

    return crime_with_ward

def load_and_preprocess_data(
    shapefile_path, crime_data_path, population_data_path,
    output_csv="merged_data_with_nans.csv"
):
    # load the ward shapefile
    wards = gpd.read_file(shapefile_path)

    # rename 'WardCode' in wards to avoid conflicts
    wards = wards.rename(columns={'WardCode': 'WardCode_w'})
    wards = _dissolve_ward_duplicates(wards)
    if 'WardCode_w' in wards.columns:
        wards['WardCode_w'] = wards['WardCode_w'].astype(str).str.strip()
    if 'WARDNAME' in wards.columns:
        wards['WARDNAME'] = wards['WARDNAME'].astype(str).str.strip()

    # load the cleaned crime data
    crime_data = pd.read_csv(crime_data_path)

    # load and prepare population data
    population_data = prepare_population_data(population_data_path)

    # convert crime data DataFrame to GeoDataFrame
    gdf_crimes = gpd.GeoDataFrame(
        crime_data,
        geometry=gpd.points_from_xy(crime_data['Longitude'], crime_data['Latitude']),
        crs="EPSG:4326"
    )

    # ensure both GeoDataFrames are using the same CRS
    if gdf_crimes.crs is not None:
        wards = wards.to_crs(gdf_crimes.crs)

    # reproject to a metric CRS for area calculation
    wards_projected = wards.to_crs(epsg=29902)  # Adjust EPSG code as necessary

    # calculate area in square kilometers for wards
    wards['area_sq_km'] = wards_projected['geometry'].area / 10 ** 6

    # perform a join without suffixes
    joined = gpd.sjoin(
        gdf_crimes,
        wards,
        how="left",
        predicate='within'
    )

    # ultil - verify columns in the joined DataFrame
    print("Columns in 'joined' DataFrame after spatial join:", joined.columns.tolist())

    # crime data by 'WardCode_w' from wards
    crime_counts = joined.groupby('WardCode_w').size().reset_index(name='NumberOfCrimes')

    #merge the crime counts with the wards shapefile
    wards_with_crime = wards.merge(
        crime_counts,
        on='WardCode_w',
        how='left'
    ).fillna(0)

    # merge population data by ward code
    wards_with_population = wards_with_crime.merge(
        population_data[['Geo_Code', 'Geo_Name', 'Population_Estimate']],
        left_on='WardCode_w',
        right_on='Geo_Code',
        how='left'
    )

    missing_count = wards_with_population['Population_Estimate'].isna().sum()
    if missing_count:
        print(f"WARNING: {missing_count} ward(s) still missing population data.")
        report = wards_with_population.loc[
            wards_with_population['Population_Estimate'].isna(),
            ['WardCode_w', 'WARDNAME']
        ].drop_duplicates()
        report_path = os.path.join('outputs', 'missing_population_report.csv')
        os.makedirs('outputs', exist_ok=True)
        report.to_csv(report_path, index=False)
        print(f"Missing population report saved to '{report_path}'.")

    # calculate the 'Population' column correctly
    wards_with_population['Population_Estimate'] = pd.to_numeric(
        wards_with_population['Population_Estimate'],
        errors='coerce'
    )
    wards_with_population['Population'] = wards_with_population['Population_Estimate']

    # calculate Crime Rate Per 100k People for each ward
    pop = wards_with_population['Population']
    pop = pd.to_numeric(pop, errors='coerce')
    crimes = wards_with_population['NumberOfCrimes']
    rates = (crimes / pop) * 100000
    rates = rates.mask(pop == 0, 0)
    wards_with_population['CrimeRatePer100kPeople'] = rates

    #output the merged df to inspect NaN values
    wards_with_population.to_csv(output_csv, index=False)

    # Return the merged GeoDataFrame
    return wards_with_population

def prepare_population_data(population_data_path, target_year=None):
    _, ext = os.path.splitext(population_data_path)
    ext = ext.lower()
    if ext in ('.xlsx', '.xls'):
        return prepare_population_data_from_excel(population_data_path)

    population_data = pd.read_csv(population_data_path)

    if target_year is None and 'Mid_Year_Ending' in population_data.columns:
        target_year = population_data['Mid_Year_Ending'].max()

    if 'Gender' in population_data.columns:
        population_data = population_data[population_data['Gender'] == 'All persons']

    if 'Age_Group' in population_data.columns:
        population_data = population_data[population_data['Age_Group'] == 'All ages']

    if target_year is not None and 'Mid_Year_Ending' in population_data.columns:
        population_data = population_data[population_data['Mid_Year_Ending'] == target_year]

    # collapse duplicates to a single population per Geo_Code
    population_data = (
        population_data
        .groupby(['Geo_Code', 'Geo_Name'], as_index=False)['Population_Estimate']
        .sum()
    )
    if 'Geo_Code' in population_data.columns:
        population_data['Geo_Code'] = population_data['Geo_Code'].astype(str).str.strip()
    if 'Geo_Name' in population_data.columns:
        population_data['Geo_Name'] = population_data['Geo_Name'].astype(str).str.strip()

    return population_data

def prepare_population_data_from_excel(population_data_path):
    ward = pd.read_excel(population_data_path, sheet_name='Ward', header=5)
    if 'Geography code' in ward.columns and 'Geography' in ward.columns:
        population_col = None
        if 'All usual residents' in ward.columns:
            population_col = 'All usual residents'
        else:
            for col in ward.columns:
                if isinstance(col, str) and 'all usual residents' in col.lower():
                    population_col = col
                    break
        if population_col is None:
            raise ValueError("Expected an 'All usual residents' column in Ward sheet.")
    else:
        ward = pd.read_excel(population_data_path, sheet_name='Ward', header=8)
        if 'Geography code' not in ward.columns:
            raise ValueError("Expected 'Geography code' column in Ward sheet.")
        population_col = 'All usual residents aged 16 and over in households'
        if population_col not in ward.columns:
            raise ValueError(f"Expected '{population_col}' column in Ward sheet.")

    ward = ward[ward['Geography code'].notna()].copy()
    ward[population_col] = pd.to_numeric(ward[population_col], errors='coerce')

    ward = (
        ward
        .groupby(['Geography code', 'Geography'], as_index=False)[population_col]
        .sum()
    )
    ward = ward.rename(
        columns={
            'Geography code': 'Geo_Code',
            'Geography': 'Geo_Name',
            population_col: 'Population_Estimate',
        }
    )
    ward['Geo_Code'] = ward['Geo_Code'].astype(str).str.strip()
    ward['Geo_Name'] = ward['Geo_Name'].astype(str).str.strip()
    return ward
