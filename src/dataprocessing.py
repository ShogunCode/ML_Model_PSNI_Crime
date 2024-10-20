import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

def clean_data(shapefile_path, crime_data_path, output_path):
    # load the ward boundaries shapefile - used to join the crime data - 
    wards = gpd.read_file(shapefile_path)

    # rename 'WardCode' in wards to avoid conflicts - issues with suffix previosuly 
    wards = wards.rename(columns={'WardCode': 'WardCode_w'})

    # load the crime data csv file
    crime_data = pd.read_csv(crime_data_path)

    # convert the crime data latitude and longitude to a GeoDataFrame
    crime_gdf = gpd.GeoDataFrame(
        crime_data,
        geometry=gpd.points_from_xy(crime_data['Longitude'], crime_data['Latitude']),
        crs="EPSG:4326"
    )

    # ensure the CRS matches
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

    # load the cleaned crime data
    crime_data = pd.read_csv(crime_data_path)

    # load the population data
    population_data = pd.read_csv(population_data_path)

    # convert crime data DataFrame to GeoDataFrame
    gdf_crimes = gpd.GeoDataFrame(
        crime_data,
        geometry=gpd.points_from_xy(crime_data['Longitude'], crime_data['Latitude']),
        crs="EPSG:4326"
    )

    # ensure both GeoDataFrames are using the same CRS
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

    # merge population data
    wards_with_population = wards_with_crime.merge(
        population_data[['Geo_Code', 'Population_Estimate']],
        left_on='WardCode_w',
        right_on='Geo_Code',
        how='left'
    )

    # calculate the 'Population' column correctly
    wards_with_population['Population'] = wards_with_population['Population_Estimate']

    # calculate Crime Rate Per 100k People for each ward
    wards_with_population['CrimeRatePer100kPeople'] = wards_with_population.apply(
        lambda row: (row['NumberOfCrimes'] / row['Population']) * 100000
        if row['Population'] > 0 else 0,
        axis=1
    )

    #output the merged df to inspect NaN values
    wards_with_population.to_csv(output_csv, index=False)

    # Return the merged GeoDataFrame
    return wards_with_population
